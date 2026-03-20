"""
database.py — SQLite storage for ToolsDNS tool metadata and embeddings.

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
        self._init_workflow_tables()

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
        # Migrations: add columns if missing (for existing databases)
        for migration in [
            "ALTER TABLE tools ADD COLUMN health_status TEXT DEFAULT 'unknown'",
            "ALTER TABLE tools ADD COLUMN category TEXT DEFAULT 'Other'",
        ]:
            try:
                conn.execute(migration)
            except Exception:
                pass

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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                label TEXT DEFAULT '',
                plan TEXT DEFAULT 'free',
                monthly_limit INTEGER DEFAULT 0,
                search_count INTEGER DEFAULT 0,
                total_searches INTEGER DEFAULT 0,
                total_tokens_used INTEGER DEFAULT 0,
                total_tokens_saved INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        # Migrations: add token tracking columns if missing
        for col in [
            "total_tokens_used INTEGER DEFAULT 0",
            "total_tokens_saved INTEGER DEFAULT 0",
        ]:
            try:
                conn.execute(f"ALTER TABLE api_keys ADD COLUMN {col}")
            except Exception:
                pass
        # Migration: add api_key column to search_log if missing
        try:
            conn.execute("ALTER TABLE search_log ADD COLUMN api_key TEXT DEFAULT ''")
        except Exception:
            pass

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
        from tooldns.categories import categorize_tool
        category = categorize_tool(name, description, source_info)
        conn.execute("""
            INSERT OR REPLACE INTO tools
            (id, name, description, input_schema, source_info, tags, embedding, indexed_at, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            tool_id, name, description,
            json.dumps(input_schema),
            json.dumps(source_info),
            json.dumps(tags),
            json.dumps(embedding),
            datetime.utcnow().isoformat(),
            category,
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

    def upsert_tools_batch(self, tools: list[dict]) -> None:
        """
        Insert or replace a batch of tools in a single transaction.

        Significantly faster than calling upsert_tool() in a loop because
        all rows are written in one commit instead of one commit per row.

        Args:
            tools: List of dicts, each with keys: tool_id, name, description,
                   input_schema, source_info, tags, embedding. All values must
                   already be Python objects (dicts/lists); JSON encoding is
                   done here.
        """
        if not tools:
            return
        now = datetime.utcnow().isoformat()
        from tooldns.categories import categorize_tool
        tool_rows = [
            (
                t["tool_id"], t["name"], t["description"],
                json.dumps(t["input_schema"]),
                json.dumps(t["source_info"]),
                json.dumps(t["tags"]),
                json.dumps(t["embedding"]),
                now,
                categorize_tool(t["name"], t["description"], t.get("source_info", {})),
            )
            for t in tools
        ]
        fts_rows = [
            (t["tool_id"], t["name"], t["description"], " ".join(t["tags"]))
            for t in tools
        ]
        tool_ids = [t["tool_id"] for t in tools]

        conn = self._get_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO tools "
            "(id, name, description, input_schema, source_info, tags, embedding, indexed_at, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tool_rows,
        )
        # Remove stale FTS entries then re-insert
        conn.executemany("DELETE FROM tools_fts WHERE tool_id = ?", [(i,) for i in tool_ids])
        conn.executemany(
            "INSERT INTO tools_fts (tool_id, name, description, tags) VALUES (?, ?, ?, ?)",
            fts_rows,
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
                "embedding": json.loads(row["embedding"]) if row["embedding"] else None,
                "indexed_at": row["indexed_at"]
            })
        return [r for r in results if r["embedding"] is not None]

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
            "tags, indexed_at, category FROM tools"
        ).fetchall()
        conn.close()

        return [{
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "input_schema": json.loads(row["input_schema"]),
            "source_info": json.loads(row["source_info"]),
            "tags": json.loads(row["tags"]),
            "indexed_at": row["indexed_at"],
            "category": row["category"] or "Other",
        } for row in rows]

    def get_categories(self) -> list[dict]:
        """Return all categories with tool counts."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT category, COUNT(*) as count FROM tools GROUP BY category ORDER BY count DESC"
        ).fetchall()
        conn.close()
        return [{"category": row["category"] or "Other", "count": row["count"]} for row in rows]

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
                   cost_saved_usd: float, search_time_ms: float,
                   api_key: str = "") -> None:
        """Record a search event with real token counts and cost savings."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO search_log
            (query, total_tools_in_index, tools_returned,
             tokens_full_index, tokens_returned, tokens_saved,
             model_name, price_per_million, cost_saved_usd,
             search_time_ms, searched_at, api_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            query, total_tools_in_index, tools_returned,
            tokens_full_index, tokens_returned, tokens_saved,
            model_name, price_per_million, cost_saved_usd,
            search_time_ms, datetime.utcnow().isoformat(), api_key
        ])
        if api_key:
            conn.execute("""
                UPDATE api_keys
                SET total_tokens_used = total_tokens_used + ?,
                    total_tokens_saved = total_tokens_saved + ?
                WHERE key = ?
            """, [tokens_returned, tokens_saved, api_key])
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

    # -----------------------------------------------------------------------
    # API Key management
    # -----------------------------------------------------------------------

    def create_api_key(self, name: str, label: str = "", plan: str = "free", monthly_limit: int = 0) -> str:
        """Create a new API key. Returns the generated key string."""
        import secrets
        key = "td_" + secrets.token_urlsafe(24)
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO api_keys (key, name, label, plan, monthly_limit) VALUES (?, ?, ?, ?, ?)",
            [key, name, label, plan, monthly_limit]
        )
        conn.commit()
        conn.close()
        return key

    def get_api_key(self, key: str) -> Optional[dict]:
        """Look up an API key. Returns None if not found."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM api_keys WHERE key = ?", [key]).fetchone()
        conn.close()
        if not row:
            return None
        return dict(row)

    def get_all_api_keys(self) -> list[dict]:
        """Return all API keys (for admin UI)."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def increment_key_usage(self, key: str) -> None:
        """Increment search_count and total_searches for a key."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET search_count = search_count + 1, total_searches = total_searches + 1, last_used_at = ? WHERE key = ?",
            [datetime.utcnow().isoformat(), key]
        )
        conn.commit()
        conn.close()

    def revoke_api_key(self, key: str) -> None:
        """Deactivate (soft-delete) an API key."""
        conn = self._get_conn()
        conn.execute("UPDATE api_keys SET is_active = 0 WHERE key = ?", [key])
        conn.commit()
        conn.close()

    def delete_api_key(self, key: str) -> None:
        """Permanently delete an API key."""
        conn = self._get_conn()
        conn.execute("DELETE FROM api_keys WHERE key = ?", [key])
        conn.commit()
        conn.close()

    def reset_key_monthly_count(self, key: str) -> None:
        """Reset monthly search_count to 0 (for billing cycle reset)."""
        conn = self._get_conn()
        conn.execute("UPDATE api_keys SET search_count = 0 WHERE key = ?", [key])
        conn.commit()
        conn.close()

    # -----------------------------------------------------------------------
    # Workflow Patterns (Smart Tool Chaining)
    # -----------------------------------------------------------------------

    def _init_workflow_tables(self):
        """Create workflow and agent preference tables."""
        conn = self._get_conn()
        # Workflow patterns
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workflow_patterns (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                trigger_phrases TEXT DEFAULT '[]',
                steps TEXT DEFAULT '[]',
                parallel_groups TEXT DEFAULT '[]',
                usage_count INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.0,
                avg_completion_time_ms REAL DEFAULT 0.0,
                source TEXT DEFAULT 'learned',
                created_by TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Workflow executions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                steps TEXT DEFAULT '[]',
                total_tokens_used INTEGER DEFAULT 0,
                started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                error TEXT DEFAULT ''
            )
        """)
        # Agent preferences
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_preferences (
                agent_id TEXT PRIMARY KEY,
                preferred_tools TEXT DEFAULT '[]',
                tool_selection_counts TEXT DEFAULT '{}',
                avg_confidence_when_selected REAL DEFAULT 0.0,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Tool call sequences (for learning)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_call_sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                session_id TEXT,
                tool_id TEXT NOT NULL,
                query TEXT DEFAULT '',
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Successful tool call arguments (for tool memory / hints)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_call_args (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                tool_id TEXT NOT NULL,
                arguments TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tool_call_args_lookup
            ON tool_call_args(agent_id, tool_id)
        """)
        conn.commit()
        conn.close()

    def upsert_workflow(self, workflow: dict) -> None:
        """Insert or update a workflow pattern."""
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO workflow_patterns (
                id, name, description, trigger_phrases, steps, parallel_groups,
                usage_count, success_rate, avg_completion_time_ms, source,
                created_by, created_at, last_used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            workflow["id"],
            workflow["name"],
            workflow.get("description", ""),
            json.dumps(workflow.get("trigger_phrases", [])),
            json.dumps(workflow.get("steps", [])),
            json.dumps(workflow.get("parallel_groups", [])),
            workflow.get("usage_count", 0),
            workflow.get("success_rate", 0.0),
            workflow.get("avg_completion_time_ms", 0.0),
            workflow.get("source", "learned"),
            workflow.get("created_by", ""),
            workflow.get("created_at", datetime.utcnow().isoformat()),
            workflow.get("last_used_at", datetime.utcnow().isoformat())
        ])
        conn.commit()
        conn.close()

    def get_workflow(self, workflow_id: str) -> Optional[dict]:
        """Get a workflow by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM workflow_patterns WHERE id = ?", [workflow_id]
        ).fetchone()
        conn.close()
        if row:
            return self._parse_workflow_row(row)
        return None

    def get_all_workflows(self, source: Optional[str] = None) -> list[dict]:
        """Get all workflows, optionally filtered by source."""
        conn = self._get_conn()
        if source:
            rows = conn.execute(
                "SELECT * FROM workflow_patterns WHERE source = ? ORDER BY usage_count DESC",
                [source]
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM workflow_patterns ORDER BY usage_count DESC"
            ).fetchall()
        conn.close()
        return [self._parse_workflow_row(r) for r in rows]

    def _parse_workflow_row(self, row: sqlite3.Row) -> dict:
        """Parse a workflow database row into a dict."""
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "trigger_phrases": json.loads(row["trigger_phrases"]),
            "steps": json.loads(row["steps"]),
            "parallel_groups": json.loads(row["parallel_groups"]),
            "usage_count": row["usage_count"],
            "success_rate": row["success_rate"],
            "avg_completion_time_ms": row["avg_completion_time_ms"],
            "source": row["source"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"]
        }

    def increment_workflow_usage(self, workflow_id: str, success: bool = True, completion_time_ms: float = 0) -> None:
        """Increment usage count and update success rate for a workflow."""
        conn = self._get_conn()
        # Get current stats
        row = conn.execute(
            "SELECT usage_count, success_rate FROM workflow_patterns WHERE id = ?",
            [workflow_id]
        ).fetchone()
        if row:
            old_count = row["usage_count"]
            old_rate = row["success_rate"]
            new_count = old_count + 1
            # Update success rate with exponential moving average
            success_val = 1.0 if success else 0.0
            new_rate = (old_rate * old_count + success_val) / new_count
            conn.execute("""
                UPDATE workflow_patterns 
                SET usage_count = ?, success_rate = ?, 
                    avg_completion_time_ms = (avg_completion_time_ms * ? + ?) / ?,
                    last_used_at = ?
                WHERE id = ?
            """, [new_count, new_rate, old_count, completion_time_ms, new_count,
                  datetime.utcnow().isoformat(), workflow_id])
            conn.commit()
        conn.close()

    def delete_workflow(self, workflow_id: str) -> None:
        """Delete a workflow pattern."""
        conn = self._get_conn()
        conn.execute("DELETE FROM workflow_patterns WHERE id = ?", [workflow_id])
        conn.commit()
        conn.close()

    def log_tool_call(self, agent_id: str, tool_id: str, query: str = "", session_id: Optional[str] = None) -> None:
        """Log a tool call for workflow learning."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO tool_call_sequences (agent_id, session_id, tool_id, query, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, [agent_id, session_id, tool_id, query, datetime.utcnow().isoformat()])
        conn.commit()
        conn.close()

    def get_recent_tool_sequences(self, agent_id: Optional[str] = None, 
                                   time_window_minutes: int = 5) -> list[list[dict]]:
        """Get sequences of tools called within time windows."""
        conn = self._get_conn()
        since = datetime.utcnow().isoformat()
        # This is a simplified version - in production, use proper time math
        if agent_id:
            rows = conn.execute("""
                SELECT agent_id, tool_id, query, timestamp 
                FROM tool_call_sequences 
                WHERE agent_id = ?
                ORDER BY agent_id, timestamp
            """, [agent_id]).fetchall()
        else:
            rows = conn.execute("""
                SELECT agent_id, tool_id, query, timestamp 
                FROM tool_call_sequences 
                ORDER BY agent_id, timestamp
            """).fetchall()
        conn.close()
        
        # Group by agent and find sequences
        sequences = []
        current_agent = None
        current_sequence = []
        
        for row in rows:
            if row["agent_id"] != current_agent:
                if len(current_sequence) >= 2:
                    sequences.append(current_sequence)
                current_agent = row["agent_id"]
                current_sequence = []
            current_sequence.append({
                "tool_id": row["tool_id"],
                "query": row["query"],
                "timestamp": row["timestamp"]
            })
        
        if len(current_sequence) >= 2:
            sequences.append(current_sequence)
        
        return sequences

    # -----------------------------------------------------------------------
    # Tool Call Args (Tool Memory / Hints)
    # -----------------------------------------------------------------------

    def log_successful_args(self, agent_id: str, tool_id: str, arguments: dict) -> None:
        """Store successful tool call args. Keeps last 5 per agent+tool (FIFO)."""
        import json as _json
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO tool_call_args (agent_id, tool_id, arguments, created_at)
            VALUES (?, ?, ?, ?)
        """, [agent_id, tool_id, _json.dumps(arguments), datetime.utcnow().isoformat()])
        # FIFO cleanup: keep only last 5
        conn.execute("""
            DELETE FROM tool_call_args WHERE id NOT IN (
                SELECT id FROM tool_call_args
                WHERE agent_id = ? AND tool_id = ?
                ORDER BY created_at DESC LIMIT 5
            ) AND agent_id = ? AND tool_id = ?
        """, [agent_id, tool_id, agent_id, tool_id])
        conn.commit()
        conn.close()

    def get_tool_hints(self, agent_id: str, tool_ids: list[str], limit: int = 1) -> dict[str, list[dict]]:
        """Batch-get last N successful arg patterns per tool.

        Returns {tool_id: [{"arguments": {...}, "at": "..."}]}.
        """
        import json as _json
        if not tool_ids:
            return {}
        conn = self._get_conn()
        placeholders = ",".join("?" for _ in tool_ids)
        rows = conn.execute(f"""
            SELECT tool_id, arguments, created_at FROM tool_call_args
            WHERE agent_id = ? AND tool_id IN ({placeholders})
            ORDER BY created_at DESC
        """, [agent_id] + tool_ids).fetchall()
        conn.close()

        result: dict[str, list[dict]] = {}
        for row in rows:
            tid = row["tool_id"]
            if tid not in result:
                result[tid] = []
            if len(result[tid]) < limit:
                result[tid].append({
                    "arguments": _json.loads(row["arguments"]),
                    "at": row["created_at"],
                })
        return result

    # -----------------------------------------------------------------------
    # Agent Preferences (Agent Memory)
    # -----------------------------------------------------------------------

    def upsert_agent_preference(self, agent_id: str, tool_id: str,
                                 confidence: float = 0.0) -> None:
        """Update agent preferences when they select a tool."""
        conn = self._get_conn()
        
        # Get current preferences
        row = conn.execute(
            "SELECT * FROM agent_preferences WHERE agent_id = ?", [agent_id]
        ).fetchone()
        
        if row:
            prefs = json.loads(row["preferred_tools"])
            counts = json.loads(row["tool_selection_counts"])
            
            # Update counts
            counts[tool_id] = counts.get(tool_id, 0) + 1
            
            # Update preferred tools list (top 20 by count)
            sorted_tools = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            prefs = [t[0] for t in sorted_tools[:20]]
            
            # Update average confidence
            old_avg = row["avg_confidence_when_selected"]
            total_selections = sum(counts.values())
            new_avg = (old_avg * (total_selections - 1) + confidence) / total_selections
            
            conn.execute("""
                UPDATE agent_preferences 
                SET preferred_tools = ?, tool_selection_counts = ?, 
                    avg_confidence_when_selected = ?, last_updated = ?
                WHERE agent_id = ?
            """, [json.dumps(prefs), json.dumps(counts), new_avg,
                  datetime.utcnow().isoformat(), agent_id])
        else:
            # Create new preference record
            conn.execute("""
                INSERT INTO agent_preferences (agent_id, preferred_tools, 
                    tool_selection_counts, avg_confidence_when_selected, last_updated)
                VALUES (?, ?, ?, ?, ?)
            """, [agent_id, json.dumps([tool_id]), json.dumps({tool_id: 1}),
                  confidence, datetime.utcnow().isoformat()])
        
        conn.commit()
        conn.close()

    def get_agent_preferences(self, agent_id: str) -> Optional[dict]:
        """Get preferences for an agent."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM agent_preferences WHERE agent_id = ?", [agent_id]
        ).fetchone()
        conn.close()
        
        if row:
            return {
                "agent_id": row["agent_id"],
                "preferred_tools": json.loads(row["preferred_tools"]),
                "tool_selection_counts": json.loads(row["tool_selection_counts"]),
                "avg_confidence_when_selected": row["avg_confidence_when_selected"],
                "last_updated": row["last_updated"]
            }
        return None

    def get_all_agent_preferences(self) -> list[dict]:
        """Get all agent preferences."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM agent_preferences").fetchall()
        conn.close()
        return [{
            "agent_id": r["agent_id"],
            "preferred_tools": json.loads(r["preferred_tools"]),
            "tool_selection_counts": json.loads(r["tool_selection_counts"]),
            "avg_confidence_when_selected": r["avg_confidence_when_selected"],
            "last_updated": r["last_updated"]
        } for r in rows]

    # -----------------------------------------------------------------------
    # Tool Call Analytics
    # -----------------------------------------------------------------------

    def get_popular_tools(self, limit: int = 20) -> list[dict]:
        """
        Get most-called tools ranked by call count.

        Returns:
            List of dicts with tool_id, tool_name, call_count, last_called.
        """
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT s.tool_id,
                   COALESCE(t.name, s.tool_id) as tool_name,
                   COUNT(*) as call_count,
                   MAX(s.timestamp) as last_called
            FROM tool_call_sequences s
            LEFT JOIN tools t ON s.tool_id = t.id
            GROUP BY s.tool_id
            ORDER BY call_count DESC
            LIMIT ?
        """, [limit]).fetchall()
        conn.close()
        return [{
            "tool_id": r["tool_id"],
            "tool_name": r["tool_name"],
            "call_count": r["call_count"],
            "last_called": r["last_called"]
        } for r in rows]

    def get_unused_tools(self) -> list[dict]:
        """
        Get tools that have been indexed but never called.

        Returns:
            List of dicts with tool_id, tool_name, description.
        """
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT t.id as tool_id, t.name as tool_name, t.description
            FROM tools t
            LEFT JOIN tool_call_sequences s ON t.id = s.tool_id
            WHERE s.tool_id IS NULL
            ORDER BY t.name
        """).fetchall()
        conn.close()
        return [{
            "tool_id": r["tool_id"],
            "tool_name": r["tool_name"],
            "description": r["description"]
        } for r in rows]

    def get_agent_tool_stats(self) -> list[dict]:
        """
        Get per-agent tool usage statistics.

        Returns:
            List of dicts with agent_id, total_calls, unique_tools, top_tools.
        """
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT agent_id,
                   COUNT(*) as total_calls,
                   COUNT(DISTINCT tool_id) as unique_tools,
                   MAX(timestamp) as last_active
            FROM tool_call_sequences
            GROUP BY agent_id
            ORDER BY total_calls DESC
        """).fetchall()

        agents = []
        for r in rows:
            # Get top tools for this agent
            top = conn.execute("""
                SELECT tool_id, COUNT(*) as cnt
                FROM tool_call_sequences
                WHERE agent_id = ?
                GROUP BY tool_id
                ORDER BY cnt DESC
                LIMIT 5
            """, [r["agent_id"]]).fetchall()

            agents.append({
                "agent_id": r["agent_id"],
                "total_calls": r["total_calls"],
                "unique_tools": r["unique_tools"],
                "last_active": r["last_active"],
                "top_tools": [{"tool_id": t["tool_id"], "calls": t["cnt"]} for t in top]
            })

        conn.close()
        return agents

    def get_recent_logs(self, limit: int = 20) -> list[dict]:
        """Get recent tool call logs."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT agent_id, tool_id, query, timestamp
            FROM tool_call_sequences
            ORDER BY timestamp DESC
            LIMIT ?
        """, [limit]).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_search_to_call_conversion(self, limit: int = 20) -> list[dict]:
        """
        Get search-to-call conversion rates per tool.

        Tools that are searched for but never called may be candidates
        for removal or improved descriptions.
        """
        conn = self._get_conn()
        # Count how many times each tool appeared in search results
        # vs how many times it was actually called
        rows = conn.execute("""
            SELECT t.id as tool_id,
                   t.name as tool_name,
                   COALESCE(calls.cnt, 0) as call_count,
                   t.indexed_at
            FROM tools t
            LEFT JOIN (
                SELECT tool_id, COUNT(*) as cnt
                FROM tool_call_sequences
                GROUP BY tool_id
            ) calls ON t.id = calls.tool_id
            ORDER BY calls.cnt DESC NULLS LAST
            LIMIT ?
        """, [limit]).fetchall()
        conn.close()
        return [{
            "tool_id": r["tool_id"],
            "tool_name": r["tool_name"],
            "call_count": r["call_count"],
            "indexed_at": r["indexed_at"]
        } for r in rows]
