"""
database.py — SQLite storage for ToolDNS tool metadata and embeddings.

This module handles all database operations for the tool index.
Each tool is stored with its full metadata and embedding vector.
The embedding is stored as JSON text in SQLite — we do cosine
similarity in Python for the MVP (can upgrade to pgvector/Qdrant later).

Architecture:
    - tools table: Stores the universal tool schema + embedding vectors
    - tools_fts table: FTS5 virtual table for BM25 keyword search
    - sources table: Tracks registered sources and their refresh status
    - SQLite is chosen for zero-dependency deployment (no external DB needed)

Usage:
    from tooldns.database import ToolDatabase
    db = ToolDatabase("./tooldns.db")
    db.upsert_tool(tool, embedding)
    results = db.get_all_tools()
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional
from tooldns.config import logger


class ToolDatabase:
    """
    SQLite database for storing tool metadata, embeddings, and source info.

    Provides CRUD operations for tools and sources. Embeddings are stored
    as JSON-serialized float arrays. Cosine similarity search is done in
    Python (see search.py) rather than in SQL for simplicity.

    Attributes:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = "./tooldns.db"):
        """
        Initialize the database and create tables if they don't exist.

        Args:
            db_path: File path for the SQLite database. Created if missing.
        """
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """
        Get a new database connection with row factory enabled.

        Returns a connection where rows behave like dicts (access by column name).

        Returns:
            sqlite3.Connection: A configured database connection.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """
        Create database tables if they don't already exist.

        Tables:
            tools: Tool metadata, input schemas, source info, embeddings, health.
            tools_fts: FTS5 virtual table for BM25 keyword search.
            sources: Tracks registered tool sources and their refresh status.
            embedding_cache: Caches description→vector mappings by model.
            ingestion_jobs: Tracks async ingestion job status.
        """
        conn = self._get_conn()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tools (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                input_schema TEXT DEFAULT '{}',
                source_info TEXT DEFAULT '{}',
                tags TEXT DEFAULT '[]',
                embedding TEXT DEFAULT '[]',
                health_status TEXT DEFAULT 'unknown',
                indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migration: add health_status column if missing (for existing databases)
        try:
            conn.execute("ALTER TABLE tools ADD COLUMN health_status TEXT DEFAULT 'unknown'")
        except Exception:
            pass  # Column already exists

        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS tools_fts
            USING fts5(tool_id, name, description, tags)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                config TEXT DEFAULT '{}',
                tools_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                health_status TEXT DEFAULT 'unknown',
                last_refreshed TEXT,
                last_health_check TEXT,
                error TEXT
            )
        """)
        # Migration: add health columns to sources if missing
        for col in ["health_status TEXT DEFAULT 'unknown'", "last_health_check TEXT"]:
            try:
                conn.execute(f"ALTER TABLE sources ADD COLUMN {col}")
            except Exception:
                pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                description_hash TEXT NOT NULL,
                model_name TEXT NOT NULL,
                embedding TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (description_hash, model_name)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                source_name TEXT,
                total_tools INTEGER DEFAULT 0,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                total_tools_in_index INTEGER NOT NULL,
                tools_returned INTEGER NOT NULL,
                tokens_full_index INTEGER NOT NULL,
                tokens_returned INTEGER NOT NULL,
                tokens_saved INTEGER NOT NULL,
                model_name TEXT DEFAULT '',
                price_per_million REAL DEFAULT 0,
                cost_saved_usd REAL DEFAULT 0,
                search_time_ms REAL DEFAULT 0,
                searched_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {self.db_path}")

    # -----------------------------------------------------------------------
    # Tool operations
    # -----------------------------------------------------------------------

    def upsert_tool(self, tool_id: str, name: str, description: str,
                    input_schema: dict, source_info: dict,
                    tags: list[str], embedding: list[float]):
        """
        Insert or update a tool in the index.

        If a tool with the same ID already exists, it gets replaced.
        This is the primary method called during ingestion.

        Args:
            tool_id: Unique identifier (format: "{source}__{tool_name}").
            name: The tool's display name.
            description: What the tool does (used for semantic search).
            input_schema: JSON Schema of the tool's parameters.
            source_info: Provenance metadata (source type, server info, etc).
            tags: Categorization tags for filtering.
            embedding: Float vector from sentence-transformers embedding.
        """
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO tools
            (id, name, description, input_schema, source_info, tags, embedding, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            tool_id, name, description,
            json.dumps(input_schema),
            json.dumps(source_info),
            json.dumps(tags),
            json.dumps(embedding),
            datetime.utcnow().isoformat()
        ])

        # Update FTS5 index (delete old entry first, then insert)
        conn.execute("DELETE FROM tools_fts WHERE tool_id = ?", [tool_id])
        tags_text = " ".join(tags) if tags else ""
        conn.execute(
            "INSERT INTO tools_fts (tool_id, name, description, tags) VALUES (?, ?, ?, ?)",
            [tool_id, name, description, tags_text]
        )

        conn.commit()
        conn.close()

    def get_all_tools_with_embeddings(self) -> list[dict]:
        """
        Retrieve all tools with their embeddings for search.

        Returns the full tool record including the embedding vector,
        which is needed for cosine similarity computation.

        Returns:
            list[dict]: All tools with metadata and embeddings.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, name, description, input_schema, source_info, "
            "tags, embedding, indexed_at FROM tools"
        ).fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "input_schema": json.loads(row["input_schema"]),
                "source_info": json.loads(row["source_info"]),
                "tags": json.loads(row["tags"]),
                "embedding": json.loads(row["embedding"]),
                "indexed_at": row["indexed_at"]
            })
        return results

    def get_tool_by_id(self, tool_id: str) -> Optional[dict]:
        """
        Get a single tool by its ID directly.

        Uses a direct SQL lookup instead of fetching all tools,
        making it O(1) instead of O(n).

        Args:
            tool_id: The tool's unique identifier.

        Returns:
            dict or None: The tool data (without embedding), or None if not found.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id, name, description, input_schema, source_info, "
            "tags, indexed_at FROM tools WHERE id = ?",
            [tool_id]
        ).fetchone()
        conn.close()

        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "input_schema": json.loads(row["input_schema"]),
            "source_info": json.loads(row["source_info"]),
            "tags": json.loads(row["tags"]),
            "indexed_at": row["indexed_at"]
        }

    def get_all_tools(self) -> list[dict]:
        """
        Retrieve all tools WITHOUT embeddings (for listing/display).

        Lighter than get_all_tools_with_embeddings() since it skips
        the embedding vectors which can be large.

        Returns:
            list[dict]: All tools with metadata (no embeddings).
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, name, description, input_schema, source_info, "
            "tags, indexed_at FROM tools"
        ).fetchall()
        conn.close()

        return [{
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "input_schema": json.loads(row["input_schema"]),
            "source_info": json.loads(row["source_info"]),
            "tags": json.loads(row["tags"]),
            "indexed_at": row["indexed_at"]
        } for row in rows]

    def get_tools_by_source(self, source_name: str) -> list[dict]:
        """
        Get all tools from a specific source.

        Useful for listing what tools a particular MCP server
        or skill directory provides.

        Args:
            source_name: The source name to filter by.

        Returns:
            list[dict]: Tools from the specified source.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, name, description, input_schema, source_info, "
            "tags, indexed_at FROM tools WHERE json_extract(source_info, '$.source_name') = ?",
            [source_name]
        ).fetchall()
        conn.close()

        return [{
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "input_schema": json.loads(row["input_schema"]),
            "source_info": json.loads(row["source_info"]),
            "tags": json.loads(row["tags"]),
            "indexed_at": row["indexed_at"]
        } for row in rows]

    def delete_tools_by_source(self, source_name: str) -> int:
        """
        Delete all tools from a specific source.

        Called before re-ingesting a source to ensure stale tools
        are removed (e.g., if a tool was removed from an MCP server).

        Args:
            source_name: The source name whose tools should be deleted.

        Returns:
            int: Number of tools deleted.
        """
        conn = self._get_conn()

        # Delete FTS5 entries FIRST (before tools, since query references tools table)
        conn.execute(
            "DELETE FROM tools_fts WHERE tool_id IN "
            "(SELECT id FROM tools WHERE json_extract(source_info, '$.source_name') = ?)",
            [source_name]
        )

        # Then delete the tools
        cursor = conn.execute(
            "DELETE FROM tools WHERE json_extract(source_info, '$.source_name') = ?",
            [source_name]
        )
        count = cursor.rowcount

        conn.commit()
        conn.close()
        return count

    def get_tool_count(self) -> int:
        """
        Get total number of indexed tools.

        Returns:
            int: Total tool count.
        """
        conn = self._get_conn()
        count = conn.execute("SELECT COUNT(*) as c FROM tools").fetchone()["c"]
        conn.close()
        return count

    def bm25_search(self, query: str, limit: int = 20) -> dict[str, float]:
        """
        Perform BM25 keyword search using FTS5.

        Returns a dict mapping tool_id to a normalized BM25 score (0-1).
        FTS5's bm25() returns negative values (lower = better match),
        so we negate and normalize them.

        Args:
            query: Search query string.
            limit: Maximum results to return.

        Returns:
            dict[str, float]: {tool_id: normalized_score} where 1.0 = best match.
        """
        conn = self._get_conn()
        try:
            # FTS5 match query — escape special chars
            safe_query = query.replace('"', '').replace("'", '')
            # Search across name and description with different weights
            rows = conn.execute(
                "SELECT tool_id, bm25(tools_fts, 0, 10.0, 5.0, 2.0) as score "
                "FROM tools_fts WHERE tools_fts MATCH ? "
                "ORDER BY score LIMIT ?",
                [safe_query, limit]
            ).fetchall()
        except Exception:
            # If FTS match fails (bad query syntax), try with quoted query
            try:
                rows = conn.execute(
                    'SELECT tool_id, bm25(tools_fts, 0, 10.0, 5.0, 2.0) as score '
                    'FROM tools_fts WHERE tools_fts MATCH ? '
                    'ORDER BY score LIMIT ?',
                    [f'"{safe_query}"', limit]
                ).fetchall()
            except Exception:
                conn.close()
                return {}
        conn.close()

        if not rows:
            return {}

        # BM25 scores are negative (lower = better), negate them
        raw_scores = {row["tool_id"]: -row["score"] for row in rows}

        # Normalize to 0-1 range
        max_score = max(raw_scores.values()) if raw_scores else 1.0
        if max_score <= 0:
            return {}

        return {
            tid: score / max_score
            for tid, score in raw_scores.items()
        }

    # -----------------------------------------------------------------------
    # Source operations
    # -----------------------------------------------------------------------

    def upsert_source(self, source_id: str, name: str, source_type: str,
                      config: dict, tools_count: int = 0, status: str = "active",
                      error: Optional[str] = None):
        """
        Insert or update a registered source.

        Args:
            source_id: Unique identifier for this source.
            name: Human-readable source name.
            source_type: Source type (mcp_config, mcp_stdio, etc).
            config: Source configuration (path, URL, command, etc).
            tools_count: Number of tools discovered.
            status: Current status (active, error, refreshing).
            error: Error message if ingestion failed.
        """
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO sources
            (id, name, type, config, tools_count, status, last_refreshed, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            source_id, name, source_type,
            json.dumps(config), tools_count, status,
            datetime.utcnow().isoformat(), error
        ])
        conn.commit()
        conn.close()

    def get_all_sources(self) -> list[dict]:
        """
        Get all registered sources.

        Returns:
            list[dict]: All sources with their config and status.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, name, type, config, tools_count, status, "
            "last_refreshed, error FROM sources"
        ).fetchall()
        conn.close()

        return [{
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "config": json.loads(row["config"]),
            "tools_count": row["tools_count"],
            "status": row["status"],
            "last_refreshed": row["last_refreshed"],
            "error": row["error"]
        } for row in rows]

    def get_source(self, source_id: str) -> Optional[dict]:
        """
        Get a specific source by ID.

        Args:
            source_id: The source's unique identifier.

        Returns:
            dict or None: The source data, or None if not found.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id, name, type, config, tools_count, status, "
            "last_refreshed, error FROM sources WHERE id = ?",
            [source_id]
        ).fetchone()
        conn.close()

        if row:
            return {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "config": json.loads(row["config"]),
                "tools_count": row["tools_count"],
                "status": row["status"],
                "last_refreshed": row["last_refreshed"],
                "error": row["error"]
            }
        return None

    def delete_source(self, source_id: str) -> bool:
        """
        Delete a source and all its tools.

        Args:
            source_id: The source to delete.

        Returns:
            bool: True if the source existed and was deleted.
        """
        source = self.get_source(source_id)
        if not source:
            return False

        self.delete_tools_by_source(source["name"])
        conn = self._get_conn()
        conn.execute("DELETE FROM sources WHERE id = ?", [source_id])
        conn.commit()
        conn.close()
        return True

    # -----------------------------------------------------------------------
    # Embedding cache — persistent vector cache (Feature 1)
    # -----------------------------------------------------------------------

    def get_cached_embedding(self, description_hash: str, model_name: str) -> Optional[list[float]]:
        """
        Look up a cached embedding by description hash and model name.

        Returns the cached vector if found, or None if not cached yet.
        The hash is computed from the tool description — if the description
        changes, the hash changes and the cache is missed (correct behavior).

        Args:
            description_hash: SHA-256 hex digest of the tool description text.
            model_name: The embedding model used (e.g., "all-MiniLM-L6-v2").

        Returns:
            list[float] or None: The cached embedding vector, or None.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT embedding FROM embedding_cache WHERE description_hash = ? AND model_name = ?",
            [description_hash, model_name]
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row["embedding"])
        return None

    def set_cached_embedding(self, description_hash: str, model_name: str,
                              embedding: list[float]) -> None:
        """
        Store an embedding in the persistent cache.

        Args:
            description_hash: SHA-256 hex digest of the tool description text.
            model_name: The embedding model used.
            embedding: The embedding vector to cache.
        """
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO embedding_cache (description_hash, model_name, embedding, created_at)
            VALUES (?, ?, ?, ?)
        """, [description_hash, model_name, json.dumps(embedding), datetime.utcnow().isoformat()])
        conn.commit()
        conn.close()

    def clear_embedding_cache(self) -> int:
        """
        Delete all cached embeddings. Useful when switching models.

        Returns:
            int: Number of cache entries deleted.
        """
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM embedding_cache")
        count = cursor.rowcount
        conn.commit()
        conn.close()
        return count

    def get_embedding_cache_stats(self) -> dict:
        """Get embedding cache statistics."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as count, COUNT(DISTINCT model_name) as models FROM embedding_cache"
        ).fetchone()
        conn.close()
        return {"cached_embeddings": row["count"], "models": row["models"]}

    # -----------------------------------------------------------------------
    # Ingestion jobs — async job tracking (Feature 4)
    # -----------------------------------------------------------------------

    def create_job(self, job_id: str, source_name: Optional[str] = None) -> None:
        """Create a new ingestion job record."""
        conn = self._get_conn()
        now = datetime.utcnow().isoformat()
        conn.execute("""
            INSERT INTO ingestion_jobs (job_id, status, source_name, created_at, updated_at)
            VALUES (?, 'pending', ?, ?, ?)
        """, [job_id, source_name, now, now])
        conn.commit()
        conn.close()

    def update_job(self, job_id: str, status: str, total_tools: int = 0,
                   error: Optional[str] = None) -> None:
        """Update an ingestion job's status and results."""
        conn = self._get_conn()
        conn.execute("""
            UPDATE ingestion_jobs
            SET status = ?, total_tools = ?, error = ?, updated_at = ?
            WHERE job_id = ?
        """, [status, total_tools, error, datetime.utcnow().isoformat(), job_id])
        conn.commit()
        conn.close()

    def get_job(self, job_id: str) -> Optional[dict]:
        """Get an ingestion job by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT job_id, status, source_name, total_tools, error, created_at, updated_at "
            "FROM ingestion_jobs WHERE job_id = ?",
            [job_id]
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "source_name": row["source_name"],
            "total_tools": row["total_tools"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def reset_stale_jobs(self) -> None:
        """Mark any 'running' jobs as 'failed' (called at startup after a crash)."""
        conn = self._get_conn()
        conn.execute("""
            UPDATE ingestion_jobs SET status = 'failed', error = 'Server restarted during job'
            WHERE status IN ('pending', 'running')
        """)
        conn.commit()
        conn.close()

    # -----------------------------------------------------------------------
    # Health status — tool and source health tracking (Feature 2)
    # -----------------------------------------------------------------------

    def set_source_health(self, source_id: str, health_status: str) -> None:
        """Update health status for a source."""
        conn = self._get_conn()
        conn.execute("""
            UPDATE sources SET health_status = ?, last_health_check = ? WHERE id = ?
        """, [health_status, datetime.utcnow().isoformat(), source_id])
        conn.commit()
        conn.close()

    def set_tools_health_by_source(self, source_name: str, health_status: str) -> None:
        """Update health status for all tools belonging to a source."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE tools SET health_status = ? "
            "WHERE json_extract(source_info, '$.source_name') = ?",
            [health_status, source_name]
        )
        conn.commit()
        conn.close()

    def get_health_summary(self) -> dict:
        """
        Get a summary of health status across all sources and tools.

        Returns:
            dict: Counts by status plus per-source health.
        """
        conn = self._get_conn()

        # Tool counts by health status
        tool_rows = conn.execute(
            "SELECT health_status, COUNT(*) as count FROM tools GROUP BY health_status"
        ).fetchall()
        tool_counts = {row["health_status"]: row["count"] for row in tool_rows}

        # Source health
        source_rows = conn.execute(
            "SELECT id, name, type, health_status, last_health_check, tools_count, status, error "
            "FROM sources ORDER BY name"
        ).fetchall()
        sources = [{
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "health_status": row["health_status"] or "unknown",
            "last_health_check": row["last_health_check"],
            "tools_count": row["tools_count"],
            "status": row["status"],
            "error": row["error"],
        } for row in source_rows]

        conn.close()

        total = sum(tool_counts.values())
        return {
            "total_tools": total,
            "healthy": tool_counts.get("healthy", 0),
            "degraded": tool_counts.get("degraded", 0),
            "down": tool_counts.get("down", 0),
            "unknown": tool_counts.get("unknown", 0),
            "sources": sources,
        }

    # -----------------------------------------------------------------------
    # Search log — per-search token savings tracking
    # -----------------------------------------------------------------------

    def log_search(self, query: str, total_tools_in_index: int,
                   tools_returned: int, tokens_full_index: int,
                   tokens_returned: int, tokens_saved: int,
                   model_name: str, price_per_million: float,
                   cost_saved_usd: float, search_time_ms: float) -> None:
        """Record a search event with real token counts and cost savings."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO search_log
            (query, total_tools_in_index, tools_returned,
             tokens_full_index, tokens_returned, tokens_saved,
             model_name, price_per_million, cost_saved_usd,
             search_time_ms, searched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            query, total_tools_in_index, tools_returned,
            tokens_full_index, tokens_returned, tokens_saved,
            model_name, price_per_million, cost_saved_usd,
            search_time_ms, datetime.utcnow().isoformat()
        ])
        conn.commit()
        conn.close()

    def get_search_stats(self) -> dict:
        """
        Aggregate token savings statistics across all logged searches.

        Returns:
            dict: Total and average savings, cost breakdown, per-model stats,
                  recent search history, and savings over time buckets.
        """
        conn = self._get_conn()

        # Overall totals
        totals = conn.execute("""
            SELECT
                COUNT(*) as total_searches,
                SUM(tokens_saved) as total_tokens_saved,
                SUM(cost_saved_usd) as total_cost_saved_usd,
                AVG(tokens_saved) as avg_tokens_saved,
                AVG(cost_saved_usd) as avg_cost_saved_usd,
                AVG(search_time_ms) as avg_search_time_ms,
                SUM(tokens_full_index) as total_tokens_would_have_used,
                SUM(tokens_returned) as total_tokens_actually_used
            FROM search_log
        """).fetchone()

        # Per-model breakdown
        model_rows = conn.execute("""
            SELECT
                model_name,
                price_per_million,
                COUNT(*) as searches,
                SUM(tokens_saved) as tokens_saved,
                SUM(cost_saved_usd) as cost_saved_usd,
                AVG(tokens_saved) as avg_tokens_saved
            FROM search_log
            WHERE model_name != ''
            GROUP BY model_name, price_per_million
            ORDER BY cost_saved_usd DESC
        """).fetchall()

        # Recent searches (last 20)
        recent_rows = conn.execute("""
            SELECT query, total_tools_in_index, tools_returned,
                   tokens_full_index, tokens_returned, tokens_saved,
                   model_name, cost_saved_usd, search_time_ms, searched_at
            FROM search_log
            ORDER BY searched_at DESC
            LIMIT 20
        """).fetchall()

        # Savings by day (last 14 days)
        daily_rows = conn.execute("""
            SELECT
                substr(searched_at, 1, 10) as day,
                COUNT(*) as searches,
                SUM(tokens_saved) as tokens_saved,
                SUM(cost_saved_usd) as cost_saved_usd
            FROM search_log
            WHERE searched_at >= datetime('now', '-14 days')
            GROUP BY day
            ORDER BY day ASC
        """).fetchall()

        conn.close()

        return {
            "total_searches": totals["total_searches"] or 0,
            "total_tokens_saved": totals["total_tokens_saved"] or 0,
            "total_cost_saved_usd": round(totals["total_cost_saved_usd"] or 0, 6),
            "avg_tokens_saved": int(totals["avg_tokens_saved"] or 0),
            "avg_cost_saved_usd": round(totals["avg_cost_saved_usd"] or 0, 6),
            "avg_search_time_ms": round(totals["avg_search_time_ms"] or 0, 1),
            "total_tokens_would_have_used": totals["total_tokens_would_have_used"] or 0,
            "total_tokens_actually_used": totals["total_tokens_actually_used"] or 0,
            "per_model": [{
                "model_name": r["model_name"],
                "price_per_million": r["price_per_million"],
                "searches": r["searches"],
                "tokens_saved": r["tokens_saved"],
                "cost_saved_usd": round(r["cost_saved_usd"], 6),
                "avg_tokens_saved": int(r["avg_tokens_saved"]),
            } for r in model_rows],
            "recent_searches": [{
                "query": r["query"],
                "total_tools": r["total_tools_in_index"],
                "returned": r["tools_returned"],
                "tokens_full": r["tokens_full_index"],
                "tokens_used": r["tokens_returned"],
                "tokens_saved": r["tokens_saved"],
                "model": r["model_name"],
                "cost_saved": round(r["cost_saved_usd"], 6),
                "time_ms": round(r["search_time_ms"], 1),
                "at": r["searched_at"],
            } for r in recent_rows],
            "daily": [{
                "day": r["day"],
                "searches": r["searches"],
                "tokens_saved": r["tokens_saved"],
                "cost_saved_usd": round(r["cost_saved_usd"], 6),
            } for r in daily_rows],
        }
