"""
ingestion.py — Tool ingestion pipeline for ToolsDNS.

This module reads tool definitions from various sources, normalizes
them into the UniversalTool format, embeds their descriptions,
and stores them in the database for semantic search.

Supported source types:
    1. MCP Config: Reads a config file (like claude_desktop_config.json) that
       lists MCP servers, then connects to each server to discover tools.
    2. MCP stdio: Connects to a single stdio-based MCP server.
    3. MCP HTTP: Connects to a single HTTP-based MCP server.
    4. Skill Directory: Reads a folder of markdown skill files and extracts
       tool definitions from their YAML headers and template sections.
    5. Custom: Registers a single manually-defined tool.

Architecture:
    Source → Fetcher → Normalize → Embed → Database

Usage:
    from tooldns.ingestion import IngestionPipeline
    pipeline = IngestionPipeline(database, embedder)
    count = pipeline.ingest_source(source_config)
    total = pipeline.ingest_all()
"""

import os
import re
import json
import hashlib
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import yaml

from tooldns.config import logger
from tooldns.database import ToolDatabase
from tooldns.embedder import Embedder
from tooldns.fetcher import MCPFetcher
from tooldns.models import SourceType


class IngestionPipeline:
    """
    Reads tools from various sources, normalizes, embeds, and indexes them.

    This is the main ingestion orchestrator. It dispatches to the right
    handler based on source type, then normalizes all discovered tools
    into the universal schema and stores them in the database.

    Attributes:
        db: The ToolDatabase instance for storage.
        embedder: The Embedder instance for generating embeddings.
        fetcher: The MCPFetcher instance for MCP protocol communication.
    """

    # Server types that use HTTP transport (sent as POST requests)
    HTTP_SERVER_TYPES = {"streamableHttp", "sse"}

    # Server names that ToolsDNS must never index — these are self-referential
    # and would pollute the index with ToolsDNS's own meta-tools.
    SELF_SERVER_NAMES = {"tooldns", "tooldns-mcp", "tool-dns"}

    def __init__(self, db: ToolDatabase, embedder: Embedder):
        """
        Initialize the ingestion pipeline.

        Args:
            db: Database instance for storing tools and sources.
            embedder: Embedder instance for generating tool description embeddings.
        """
        self.db = db
        self.embedder = embedder
        self.fetcher = MCPFetcher()

    # -------------------------------------------------------------------
    # Env var resolution
    # -------------------------------------------------------------------

    def _resolve_env_vars(self, value):
        """
        Resolve ${VAR} and $VAR environment variable placeholders.

        Works recursively on strings, dicts, and lists. Used to expand
        env var references in config files (e.g., uses ${COMPOSIO_API_KEY} in headers).

        Args:
            value: A string, dict, list, or other value. Strings get
                   env var substitution, dicts/lists are processed recursively.

        Returns:
            The value with all env var references resolved.
        """
        if isinstance(value, str):
            # Replace ${VAR} patterns
            result = re.sub(
                r'\$\{([^}]+)\}',
                lambda m: os.environ.get(m.group(1), m.group(0)),
                value
            )
            # Replace $VAR patterns (word boundary)
            result = re.sub(
                r'\$([A-Z_][A-Z0-9_]*)',
                lambda m: os.environ.get(m.group(1), m.group(0)),
                result
            )
            return result
        elif isinstance(value, dict):
            return {k: self._resolve_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._resolve_env_vars(v) for v in value]
        return value

    # -------------------------------------------------------------------
    # Main entry points
    # -------------------------------------------------------------------

    def ingest_source(self, source_config: dict) -> int:
        """
        Ingest tools from a single source configuration.

        Dispatches to the appropriate handler based on source type,
        then indexes all discovered tools.

        Args:
            source_config: Source configuration dict with at least:
                - type (str): The source type (see SourceType enum).
                - name (str): Human-readable source name.
                Plus type-specific fields (path, url, command, etc).

        Returns:
            int: Number of tools successfully ingested.

        Raises:
            ValueError: If the source type is not supported.
        """
        source_type = source_config.get("type", "")
        source_name = source_config.get("name", "unknown")
        source_id = self._make_source_id(source_config)

        logger.info(f"Ingesting source: {source_name} (type: {source_type})")

        try:
            # Clear old tools from this source before re-ingesting
            removed = self.db.delete_tools_by_source(source_name)
            if removed:
                logger.info(f"Removed {removed} old tools from {source_name}")

            # Dispatch to the right handler
            if source_type == SourceType.MCP_CONFIG:
                tools = self._ingest_mcp_config(source_config)
            elif source_type == SourceType.MCP_STDIO:
                tools = self._ingest_mcp_stdio(source_config)
            elif source_type == SourceType.MCP_HTTP:
                tools = self._ingest_mcp_http(source_config)
            elif source_type == SourceType.SKILL_DIRECTORY:
                tools = self._ingest_skill_directory(source_config)
            elif source_type == SourceType.CUSTOM:
                tools = self._ingest_custom(source_config)
            else:
                raise ValueError(f"Unsupported source type: {source_type}")

            # Embed and store each tool
            count = self._index_tools(tools, source_name, source_type)

            # Update source record
            self.db.upsert_source(
                source_id=source_id,
                name=source_name,
                source_type=source_type,
                config=source_config,
                tools_count=count,
                status="active"
            )

            logger.info(f"Successfully ingested {count} tools from {source_name}")
            return count

        except Exception as e:
            logger.error(f"Failed to ingest {source_name}: {e}")
            self.db.upsert_source(
                source_id=source_id,
                name=source_name,
                source_type=source_type,
                config=source_config,
                tools_count=0,
                status="error",
                error=str(e)
            )
            raise

    def _cleanup_orphaned_tools(self) -> None:
        """
        Delete tools whose source_name no longer exists in the sources table.

        Called at the start of ingest_all so deleted sources don't leave
        stale tools behind in the index.
        """
        import sqlite3 as _sqlite3
        db_path = self.db.db_path
        conn = _sqlite3.connect(db_path)
        try:
            active = {r[0] for r in conn.execute("SELECT name FROM sources").fetchall()}
            orphaned_rows = conn.execute(
                "SELECT DISTINCT json_extract(source_info, '$.source_name') FROM tools"
            ).fetchall()
            orphaned = {r[0] for r in orphaned_rows if r[0] and r[0] not in active}
            for sn in orphaned:
                conn.execute(
                    "DELETE FROM tools_fts WHERE tool_id IN "
                    "(SELECT id FROM tools WHERE json_extract(source_info, '$.source_name') = ?)",
                    [sn]
                )
                c = conn.execute(
                    "DELETE FROM tools WHERE json_extract(source_info, '$.source_name') = ?", [sn]
                )
                if c.rowcount:
                    logger.info(f"Cleaned up {c.rowcount} orphaned tools from deleted source '{sn}'")
            conn.commit()
        except Exception as e:
            logger.warning(f"Orphan cleanup error: {e}")
        finally:
            conn.close()

    def ingest_all(self) -> int:
        """
        Re-ingest all registered sources AND local config directories.

        Reads the list of sources from the database, re-ingests each one,
        then scans ~/.tooldns/ for local configs, skills, and tools.

        Returns:
            int: Total number of tools ingested across all sources.
        """
        # Remove tools from sources that no longer exist in the DB
        self._cleanup_orphaned_tools()

        sources = self.db.get_all_sources()
        total = 0
        errors = []

        for source in sources:
            try:
                config = source["config"]
                config["name"] = source["name"]
                config["type"] = source["type"]

                # If the DB ID doesn't match the computed hash, the entry is stale
                # (created by an old code path). Delete it before re-ingesting so
                # the correct hash-based ID takes over and no duplicates form.
                expected_id = self._make_source_id(config)
                if source["id"] != expected_id:
                    logger.info(
                        f"Removing stale source ID {source['id']} for '{source['name']}' "
                        f"(expected {expected_id})"
                    )
                    self.db.delete_source(source["id"])
                    continue  # The correct entry will be created when ingest_local runs

                count = self.ingest_source(config)
                total += count
            except Exception as e:
                errors.append(f"{source['name']}: {e}")
                logger.error(f"Error re-ingesting {source['name']}: {e}")

        # Also ingest local config directories
        try:
            local_count = self.ingest_local()
            total += local_count
        except Exception as e:
            logger.error(f"Error ingesting local config: {e}")

        if errors:
            logger.warning(f"Ingestion errors: {errors}")

        logger.info(f"Full re-ingestion complete: {total} tools from {len(sources)} sources + local")
        return total

    def ingest_local(self) -> int:
        """
        Ingest tools from ~/.tooldns/ local directories only.

        Scans:
            1. ~/.tooldns/config.json — custom MCP servers
            2. ~/.tooldns/skills/ — local skill files
            3. ~/.tooldns/tools/ — custom Python tool definitions

        Returns:
            int: Total tools ingested from local directories.
        """
        from tooldns.config import TOOLDNS_HOME
        home = TOOLDNS_HOME
        total = 0

        # 1. Custom MCP config from config.json
        config_file = home / "config.json"
        if config_file.exists():
            try:
                config_data = json.loads(config_file.read_text(encoding="utf-8"))
                if config_data.get("mcpServers"):
                    count = self._ingest_local_config(config_file)
                    total += count
                    logger.info(f"Local config.json: {count} MCP tools")
            except Exception as e:
                logger.error(f"Local config.json error: {e}")

        # 2. Built-in skills directory
        skills_dir = home / "skills"
        if skills_dir.exists():
            try:
                count = self._ingest_local_skills(skills_dir)
                total += count
                logger.info(f"Local skills/: {count} tools")
            except Exception as e:
                logger.error(f"Local skills/ error: {e}")

        # 3. Custom tools directory
        tools_dir = home / "tools"
        if tools_dir.exists():
            try:
                count = self._ingest_local_tools(tools_dir)
                total += count
                logger.info(f"Local tools/: {count} tools")
            except Exception as e:
                logger.error(f"Local tools/ error: {e}")

        return total

    def _ingest_local_config(self, config_path: Path) -> int:
        """
        Ingest MCP servers from ~/.tooldns/config.json.

        Skips if the source was explicitly disabled (deleted by user).

        Args:
            config_path: Path to the config.json file.

        Returns:
            int: Number of tools ingested.
        """
        source_name = "tooldns"
        if self._is_source_disabled(source_name):
            logger.info(f"Skipping disabled source: {source_name}")
            return 0
        config = {
            "type": SourceType.MCP_CONFIG.value,
            "name": source_name,
            "path": str(config_path),
            "config_key": "mcpServers",
        }
        return self.ingest_source(config)

    def _is_source_disabled(self, source_name: str) -> bool:
        """Check if a source was explicitly deleted by the user."""
        from tooldns.config import TOOLDNS_HOME
        disabled_file = TOOLDNS_HOME / "disabled_sources.json"
        if not disabled_file.exists():
            return False
        try:
            disabled = json.loads(disabled_file.read_text())
            return source_name in disabled
        except Exception:
            return False

    @staticmethod
    def disable_source(source_name: str):
        """Mark a source as disabled so ingest_local skips it. Called on delete."""
        from tooldns.config import TOOLDNS_HOME
        disabled_file = TOOLDNS_HOME / "disabled_sources.json"
        try:
            disabled = json.loads(disabled_file.read_text()) if disabled_file.exists() else []
            if source_name not in disabled:
                disabled.append(source_name)
                disabled_file.write_text(json.dumps(disabled, indent=2))
        except Exception as e:
            logger.warning(f"Could not update disabled_sources.json: {e}")

    @staticmethod
    def enable_source(source_name: str):
        """Remove a source from the disabled list (called when re-adding)."""
        from tooldns.config import TOOLDNS_HOME
        disabled_file = TOOLDNS_HOME / "disabled_sources.json"
        if not disabled_file.exists():
            return
        try:
            disabled = json.loads(disabled_file.read_text())
            if source_name in disabled:
                disabled.remove(source_name)
                disabled_file.write_text(json.dumps(disabled, indent=2))
        except Exception as e:
            logger.warning(f"Could not update disabled_sources.json: {e}")

    def _ingest_local_skills(self, skills_dir: Path,
                              source_name: str = "tooldns-skills") -> int:
        """
        Ingest skills from ~/.tooldns/skills/ — SKILL.md files AND Python tool scripts.

        Delegates to ingest_source with SKILL_DIRECTORY type so that both
        skill descriptions and Python MCP tools within skill folders are indexed.

        Args:
            skills_dir: Path to the skills directory.
            source_name: Source name for tracking (default: "tooldns-skills").

        Returns:
            int: Number of tools ingested.
        """
        config = {
            "type": SourceType.SKILL_DIRECTORY.value,
            "name": source_name,
            "path": str(skills_dir),
        }
        return self.ingest_source(config)
        return count

    def _parse_skill_md(self, content: str, folder_name: str) -> tuple[str, str]:
        """
        Parse a SKILL.md file for name and description.

        Extracts YAML front matter (between --- delimiters) for the
        skill name and description. Falls back to folder name.

        Args:
            content: The full SKILL.md file content.
            folder_name: The skill folder name (used as fallback).

        Returns:
            tuple[str, str]: (name, description)
        """
        name = folder_name
        description = ""

        # Parse YAML front matter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                body = parts[2].strip()

                for line in frontmatter.strip().split("\n"):
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"').strip("'")

                # Use body as description if frontmatter description is short
                if body and len(description) < 50:
                    description = (description + " " + body[:500]).strip()
        else:
            # No front matter — use first line as description
            lines = content.strip().split("\n")
            description = lines[0].lstrip("# ").strip() if lines else folder_name

        if not description:
            description = f"Skill: {name}"

        return name, description

    def _ingest_local_tools(self, tools_dir: Path) -> int:
        """
        Ingest custom tools from ~/.tooldns/tools/*.py files.

        Each .py file should define module-level variables:
            - TOOL_NAME (str): The tool's name
            - TOOL_DESCRIPTION (str): What the tool does
            - TOOL_INPUT_SCHEMA (dict): JSON Schema for inputs (optional)

        Example tools/my_tool.py:
            TOOL_NAME = "my_custom_tool"
            TOOL_DESCRIPTION = "Does something useful"
            TOOL_INPUT_SCHEMA = {"type": "object", "properties": {...}}

        Args:
            tools_dir: Path to the tools directory.

        Returns:
            int: Number of tools ingested.
        """
        source_name = "local-tools"
        self.db.delete_tools_by_source(source_name)

        tools = []
        for tool_file in sorted(tools_dir.glob("*.py")):
            try:
                content = tool_file.read_text(encoding="utf-8")
                # Extract module-level variables without executing the file
                name, description, schema, mcp_server = self._parse_tool_py(content, tool_file.stem)
                tool = {
                    "name": name,
                    "description": description,
                    "inputSchema": schema,
                    "_source_server": mcp_server or "local-tools",
                    "_source_type": "mcp_config" if mcp_server else "custom",
                }
                if mcp_server:
                    tool["_mcp_server"] = mcp_server
                tools.append(tool)
                logger.info(f"  → Tool: {name}" + (f" (via {mcp_server})" if mcp_server else ""))
            except Exception as e:
                logger.warning(f"  ✗ Tool {tool_file.name}: {e}")

        return self._index_tools(tools, source_name, "custom")

    def _parse_tool_py(self, content: str, filename: str) -> tuple[str, str, dict, str]:
        """
        Parse a custom tool .py file for name, description, schema, and MCP server.

        Extracts TOOL_NAME, TOOL_DESCRIPTION, TOOL_INPUT_SCHEMA, and optionally
        TOOL_MCP_SERVER from module-level variable assignments without executing code.

        Set TOOL_MCP_SERVER to route the tool call through an existing MCP server,
        e.g. TOOL_MCP_SERVER = "browser-use" makes how_to_call point to that server.

        Args:
            content: The .py file contents.
            filename: The filename (used as fallback name).

        Returns:
            tuple[str, str, dict, str]: (name, description, input_schema, mcp_server)
        """
        import ast

        name = filename
        description = ""
        schema = {}
        mcp_server = ""

        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            if target.id == "TOOL_NAME" and isinstance(node.value, ast.Constant):
                                name = node.value.value
                            elif target.id == "TOOL_DESCRIPTION" and isinstance(node.value, ast.Constant):
                                description = node.value.value
                            elif target.id == "TOOL_INPUT_SCHEMA":
                                schema = ast.literal_eval(node.value)
                            elif target.id == "TOOL_MCP_SERVER" and isinstance(node.value, ast.Constant):
                                mcp_server = node.value.value
        except Exception:
            # Fallback: use docstring
            lines = content.strip().split("\n")
            for line in lines:
                if line.strip().startswith('"""') or line.strip().startswith("'''"):
                    description = line.strip().strip('"\"').strip("'\'")
                    break

        if not description:
            description = f"Custom tool: {name}"

        return name, description, schema, mcp_server

    # -------------------------------------------------------------------
    # Source-specific handlers
    # -------------------------------------------------------------------

    def _ingest_mcp_config(self, config: dict) -> list[dict]:
        """
        Read an MCP config file and discover tools from all listed servers.

        Parses a JSON config file (like claude_desktop_config.json), finds the
        MCP servers section, and fetches tools from each server.

        Config fields:
            path (str): Path to the config file.
            config_key (str): Dot-separated JSON path to MCP servers
                              (default: "tools.mcpServers").

        Args:
            config: Source configuration dict.

        Returns:
            list[dict]: Normalized tool dicts from all discovered servers.
        """
        config_path = Path(os.path.expanduser(config.get("path", "")))
        config_key = config.get("config_key", "tools.mcpServers")

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        raw_config = json.loads(config_path.read_text(encoding="utf-8"))

        # Navigate to the mcpServers section using dot notation
        mcp_section = raw_config
        for key in config_key.split("."):
            mcp_section = mcp_section.get(key, {})

        if not mcp_section:
            raise ValueError(
                f"No MCP servers found at '{config_key}' in {config_path}"
            )

        skip_servers = set(config.get("skip_servers", [])) | self.SELF_SERVER_NAMES

        # Collect servers to fetch (skip excluded ones up front)
        servers_to_fetch = [
            (name, cfg) for name, cfg in mcp_section.items()
            if name not in skip_servers
        ]
        for name in mcp_section:
            if name in skip_servers:
                logger.info(f"Skipping MCP server (excluded): {name}")

        def _fetch_one(server_name: str, server_config: dict) -> tuple[str, list[dict]]:
            """Fetch tools from one server; returns (server_name, tools)."""
            server_type = server_config.get("type", "stdio")
            if server_type in self.HTTP_SERVER_TYPES:
                url = self._resolve_env_vars(server_config.get("url", ""))
                headers = self._resolve_env_vars(server_config.get("headers"))
                tools = self.fetcher.fetch_http(url=url, headers=headers)
                for tool in tools:
                    tool["_source_server"] = server_name
                    tool["_source_type"] = server_type
                    tool["_url"] = url
                    tool["_headers"] = headers or {}
            elif server_config.get("command"):
                command = server_config.get("command", "python3")
                args = self._resolve_env_vars(server_config.get("args", []))
                tools = self.fetcher.fetch_stdio(command=command, args=args)
                for tool in tools:
                    tool["_source_server"] = server_name
                    tool["_source_type"] = "stdio"
                    tool["_command"] = command
                    tool["_args"] = args
            else:
                logger.warning(f"  ⚠ {server_name}: unknown server type '{server_type}', skipping")
                tools = []
            return server_name, tools

        all_tools = []
        max_workers = min(8, len(servers_to_fetch)) if servers_to_fetch else 1
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_fetch_one, name, cfg): name
                for name, cfg in servers_to_fetch
            }
            for future in as_completed(futures):
                server_name = futures[future]
                logger.info(f"Fetching tools from MCP server: {server_name}")
                try:
                    _, tools = future.result()
                    all_tools.extend(tools)
                    logger.info(f"  → {server_name}: {len(tools)} tool(s)")
                except Exception as e:
                    logger.warning(f"  ✗ {server_name}: {e}")

        return all_tools

    def _ingest_mcp_stdio(self, config: dict) -> list[dict]:
        """
        Fetch tools from a single stdio-based MCP server.

        Config fields:
            command (str): The command to run (e.g., "python3").
            args (list[str]): Command arguments.

        Args:
            config: Source configuration dict.

        Returns:
            list[dict]: Tools from the MCP server.
        """
        command = config.get("command", "python3")
        args = config.get("args", [])
        tools = self.fetcher.fetch_stdio(command, args)

        for tool in tools:
            tool["_source_server"] = config.get("name", "unknown")
            tool["_source_type"] = "stdio"

        return tools

    def _ingest_mcp_http(self, config: dict) -> list[dict]:
        """
        Fetch tools from a single HTTP-based MCP server.

        Config fields:
            url (str): The MCP server's HTTP endpoint.
            headers (dict): Optional HTTP headers.

        Args:
            config: Source configuration dict.

        Returns:
            list[dict]: Tools from the MCP server.
        """
        url = self._resolve_env_vars(config.get("url", ""))
        headers = self._resolve_env_vars(config.get("headers"))
        tools = self.fetcher.fetch_http(url=url, headers=headers)

        for tool in tools:
            tool["_source_server"] = config.get("name", "unknown")
            tool["_source_type"] = "http"

        return tools

    def _ingest_skill_directory(self, config: dict) -> list[dict]:
        """
        Read a directory of skill markdown files and extract tool definitions.

        Parses each .md file in the directory, extracting the YAML header
        (name, description) and any TEMPLATE sections as individual tools.

        Reads ToolsDNS skill markdown format.

        Config fields:
            path (str): Path to the directory containing .md skill files.

        Args:
            config: Source configuration dict.

        Returns:
            list[dict]: Normalized tool dicts from all skill files.
        """
        dir_path = Path(os.path.expanduser(config.get("path", "")))

        if not dir_path.exists():
            raise FileNotFoundError(f"Skill directory not found: {dir_path}")

        tools = []

        for item in sorted(dir_path.iterdir()):
            # Pattern 1: subfolder with SKILL.md (e.g. daily-standup/SKILL.md)
            if item.is_dir():
                skill_file = item / "SKILL.md"
                if not skill_file.exists():
                    continue
                try:
                    content = skill_file.read_text(encoding="utf-8")
                    name, description = self._parse_skill_md(content, item.name)
                    tools.append({
                        "name": name,
                        "description": description,
                        "inputSchema": {},
                        "_source_server": config.get("name", "skills"),
                        "_source_type": "skill",
                        "_skill_folder": str(item),
                    })
                    logger.info(f"  → Skill: {name}")
                except Exception as e:
                    logger.warning(f"Error parsing skill {item.name}: {e}")

                # Auto-detect tool scripts inside the skill folder (parallel)
                skill_scripts = sorted(item.glob("*.py"))
                if skill_scripts:
                    source_name_val = config.get("name", "skills")

                    def _fetch_script(script, src=source_name_val, folder=str(item)):
                        try:
                            logger.info(f"  Found tool script: {item.name}/{script.name}")
                            script_tools = self.fetcher.fetch_stdio(
                                "python3", ["-u", str(script)], timeout=5
                            )
                            for t in script_tools:
                                t["_source_server"] = src
                                t["_source_type"] = "skill_tool_stdio"
                                t["_command"] = "python3"
                                t["_args"] = [str(script)]
                                t["_skill_folder"] = folder
                            logger.info(f"    → {len(script_tools)} MCP tools from {script.name}")
                            return script_tools
                        except Exception as e:
                            logger.debug(f"    {script.name} not MCP, trying static parse: {e}")
                            try:
                                sname, sdesc, sschema, _ = self._parse_tool_py(
                                    script.read_text(encoding="utf-8"), script.stem
                                )
                                logger.info(f"    → Static tool: {sname}")
                                return [{
                                    "name": sname,
                                    "description": sdesc,
                                    "inputSchema": sschema,
                                    "_source_server": src,
                                    "_source_type": "skill_tool_script",
                                    "_command": "python3",
                                    "_args": [str(script)],
                                    "_skill_folder": folder,
                                }]
                            except Exception as e2:
                                logger.warning(f"    Could not parse {script.name}: {e2}")
                                return []

                    with ThreadPoolExecutor(max_workers=min(4, len(skill_scripts))) as pool:
                        for script_tools in pool.map(_fetch_script, skill_scripts):
                            tools.extend(script_tools)

            # Pattern 2: flat .md file (e.g. github.md)
            elif item.is_file() and item.suffix == ".md" and not item.name.startswith("_"):
                try:
                    content = item.read_text(encoding="utf-8")
                    name, description = self._parse_skill_md(content, item.stem)
                    tools.append({
                        "name": name,
                        "description": description,
                        "inputSchema": {},
                        "_source_server": config.get("name", "skills"),
                        "_source_type": "skill",
                    })
                except Exception as e:
                    logger.warning(f"Error parsing skill file {item.name}: {e}")

        return tools

    def _ingest_custom(self, config: dict) -> list[dict]:
        """
        Register a single custom tool from user-provided data.

        Config fields:
            tool_name (str): The tool's name.
            tool_description (str): What the tool does.
            tool_schema (dict): The tool's input schema.

        Args:
            config: Source configuration dict.

        Returns:
            list[dict]: A single-item list containing the custom tool.
        """
        return [{
            "name": config.get("tool_name", "custom_tool"),
            "description": config.get("tool_description", "A custom tool"),
            "inputSchema": config.get("tool_schema", {}),
            "_source_server": config.get("name", "custom"),
            "_source_type": "custom"
        }]

    # -------------------------------------------------------------------
    # Indexing — normalize and store tools
    # -------------------------------------------------------------------

    def _index_tools(self, raw_tools: list[dict], source_name: str,
                     source_type: str) -> int:
        """
        Normalize, embed, and store a list of raw tool definitions.

        Takes tool definitions in MCP format (name, description, inputSchema)
        and converts them into the universal ToolsDNS format, generates
        embeddings for search, and stores them in the database.

        Args:
            raw_tools: List of raw tool dicts from a source (MCP format).
            source_name: The source's human-readable name.
            source_type: The source type (for provenance tracking).

        Returns:
            int: Number of tools successfully indexed.
        """
        if not raw_tools:
            return 0

        # Build descriptions and check embedding cache
        descriptions = [
            f"{t.get('name', '')}: {t.get('description', '')}"
            for t in raw_tools
        ]

        # Persistent vector cache: compute hash per description, only embed cache misses
        desc_hashes = [hashlib.sha256(d.encode()).hexdigest() for d in descriptions]
        model_name = self.embedder.model_name
        cached = [self.db.get_cached_embedding(h, model_name) for h in desc_hashes]

        # Identify which descriptions need new embeddings
        missing_idx = [i for i, c in enumerate(cached) if c is None]
        if missing_idx:
            missing_texts = [descriptions[i] for i in missing_idx]
            new_embeddings = self.embedder.embed_batch(missing_texts)
            for i, embedding in zip(missing_idx, new_embeddings):
                self.db.set_cached_embedding(desc_hashes[i], model_name, embedding)
                cached[i] = embedding
            logger.info(f"Embedded {len(missing_idx)} new tools, {len(descriptions) - len(missing_idx)} from cache")
        else:
            logger.info(f"All {len(descriptions)} embeddings served from cache")

        embeddings = cached

        batch = []
        for tool, embedding in zip(raw_tools, embeddings):
            name = tool.get("name", "unknown")
            server = tool.get("_source_server", source_name)
            tool_id = f"{source_name}__{name}"

            source_info = {
                "source_type": source_type,
                "source_name": source_name,
                "original_name": name,
                "server": server
            }
            if tool.get("_command"):
                source_info["command"] = tool["_command"]
                source_info["args"] = tool.get("_args", [])
            if tool.get("_url"):
                source_info["url"] = tool["_url"]
                source_info["headers"] = tool.get("_headers", {})
            if tool.get("_skill_folder"):
                source_info["skill_folder"] = tool["_skill_folder"]

            batch.append({
                "tool_id": tool_id,
                "name": name,
                "description": tool.get("description", ""),
                "input_schema": tool.get("inputSchema", {}),
                "source_info": source_info,
                "tags": self._extract_tags(name, tool.get("description", "")),
                "embedding": embedding,
            })

        self.db.upsert_tools_batch(batch)
        return len(batch)

    def _extract_tags(self, name: str, description: str) -> list[str]:
        """
        Auto-generate tags from a tool's name and description.

        Extracts keywords by splitting the tool name on underscores
        and filtering common words. These tags can be used for
        non-semantic filtering.

        Args:
            name: The tool name.
            description: The tool description.

        Returns:
            list[str]: Extracted tags.
        """
        stop_words = {"a", "an", "the", "to", "of", "in", "for", "is", "on", "with"}
        words = set()

        for part in name.lower().replace("-", "_").split("_"):
            if part and part not in stop_words and len(part) > 1:
                words.add(part)

        return sorted(words)[:10]

    def _make_source_id(self, config: dict) -> str:
        """
        Generate a stable unique ID for a source configuration.

        Uses a hash of the source name and type so the same source
        can be re-registered without creating duplicates.

        Note: In Python 3.12+, str(StrEnum.MEMBER) returns the class-qualified
        name ("SourceType.MCP_CONFIG"), not the value ("mcp_config"). We
        explicitly use .value to get the raw string so the hash is consistent
        regardless of whether the type field holds a StrEnum or a plain string.

        Args:
            config: The source configuration dict.

        Returns:
            str: A stable source ID.
        """
        name = config.get("name", "")
        stype = config.get("type", "")
        # StrEnum in Python 3.12 doesn't coerce to value in f-strings
        if hasattr(stype, "value"):
            stype = stype.value
        key = f"{name}:{stype}"
        return hashlib.md5(key.encode()).hexdigest()[:12]
