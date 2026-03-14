"""
database.py — SQLite storage for ToolDNS tool metadata and embeddings.

This module handles all database operations for the tool index.
Each tool is stored with its full metadata and embedding vector.
The embedding is stored as JSON text in SQLite — we do cosine
similarity in Python for the MVP (can upgrade to pgvector/Qdrant later).

Architecture:
    - tools table: Stores the universal tool schema + embedding vectors
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
            tools: Stores tool metadata, input schemas, source info, and embeddings.
            sources: Tracks registered tool sources and their refresh status.
        """
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tools (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                input_schema TEXT DEFAULT '{}',
                source_info TEXT DEFAULT '{}',
                tags TEXT DEFAULT '[]',
                embedding TEXT DEFAULT '[]',
                indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                config TEXT DEFAULT '{}',
                tools_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                last_refreshed TEXT,
                error TEXT
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
