"""
ingestion.py — Tool ingestion pipeline for ToolDNS.

This module reads tool definitions from various sources, normalizes
them into the UniversalTool format, embeds their descriptions,
and stores them in the database for semantic search.

Supported source types:
    1. MCP Config: Reads a config file (like nanobot's config.json) that
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
        env var references in config files (e.g., openclaw's mcporter.json
        uses ${COMPOSIO_API_KEY} in headers).

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

    def ingest_all(self) -> int:
        """
        Re-ingest all registered sources.

        Reads the list of sources from the database and re-ingests each one.
        This is called by the refresh cron and the /v1/ingest endpoint.

        Returns:
            int: Total number of tools ingested across all sources.
        """
        sources = self.db.get_all_sources()
        total = 0
        errors = []

        for source in sources:
            try:
                config = source["config"]
                config["name"] = source["name"]
                config["type"] = source["type"]
                count = self.ingest_source(config)
                total += count
            except Exception as e:
                errors.append(f"{source['name']}: {e}")
                logger.error(f"Error re-ingesting {source['name']}: {e}")

        if errors:
            logger.warning(f"Ingestion errors: {errors}")

        logger.info(f"Full re-ingestion complete: {total} tools from {len(sources)} sources")
        return total

    # -------------------------------------------------------------------
    # Source-specific handlers
    # -------------------------------------------------------------------

    def _ingest_mcp_config(self, config: dict) -> list[dict]:
        """
        Read an MCP config file and discover tools from all listed servers.

        Parses a JSON config file (like nanobot's config.json), finds the
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

        all_tools = []
        for server_name, server_config in mcp_section.items():
            logger.info(f"Fetching tools from MCP server: {server_name}")
            try:
                server_type = server_config.get("type", "stdio")
                if server_type in self.HTTP_SERVER_TYPES:
                    # Resolve env vars in URL and headers
                    url = self._resolve_env_vars(server_config.get("url", ""))
                    headers = self._resolve_env_vars(server_config.get("headers"))
                    tools = self.fetcher.fetch_http(url=url, headers=headers)
                elif server_config.get("command"):
                    # stdio server — resolve env vars in args too
                    args = self._resolve_env_vars(server_config.get("args", []))
                    tools = self.fetcher.fetch_stdio(
                        command=server_config.get("command", "python3"),
                        args=args
                    )
                else:
                    logger.warning(f"  ⚠ {server_name}: unknown server type '{server_type}', skipping")
                    continue

                # Tag each tool with its server name
                for tool in tools:
                    tool["_source_server"] = server_name
                    tool["_source_type"] = server_type

                all_tools.extend(tools)
                logger.info(f"  → {server_name}: {len(tools)} tools")

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

        This is compatible with the nanobot-skills-engine skill format.

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
        for md_file in dir_path.glob("*.md"):
            if md_file.name.startswith("_"):
                continue  # Skip index files

            try:
                content = md_file.read_text(encoding="utf-8")
                file_tools = self._parse_skill_file(content, md_file.stem)
                tools.extend(file_tools)
            except Exception as e:
                logger.warning(f"Error parsing skill file {md_file}: {e}")

        return tools

    def _parse_skill_file(self, content: str, filename: str) -> list[dict]:
        """
        Parse a single skill markdown file into tool definitions.

        Extracts the YAML header for the skill name and description,
        then finds each ## Section with a TEMPLATE block and creates
        a tool definition for each one.

        Args:
            content: The markdown file content.
            filename: The filename (without extension) for fallback naming.

        Returns:
            list[dict]: Tool dicts extracted from the skill file.
        """
        # Parse YAML header
        header = {}
        header_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
        if header_match:
            header = yaml.safe_load(header_match.group(1)) or {}

        skill_name = header.get("name", filename)
        skill_desc = header.get("description", "")

        tools = []

        # Find all ## sections with TEMPLATE blocks
        sections = re.finditer(
            r"## (.+?)\nWHEN: (.+?)\nTEMPLATE:\n(.*?)(?=\n## |\Z)",
            content, re.DOTALL
        )

        for match in sections:
            action_name = match.group(1).strip()
            when_desc = match.group(2).strip()
            template = match.group(3).strip()

            # Extract variables from EXTRACT section
            extract_match = re.search(
                r"EXTRACT:\n(.*?)(?=EXAMPLE:|$)", template, re.DOTALL
            )
            properties = {}
            if extract_match:
                for line in extract_match.group(1).strip().split("\n"):
                    line = line.strip()
                    if ":" in line and not line.startswith("("):
                        key, desc = line.split(":", 1)
                        properties[key.strip()] = {
                            "type": "string",
                            "description": desc.strip()
                        }

            tool = {
                "name": f"{skill_name.lower().replace(' ', '_')}_{action_name.lower().replace(' ', '_')}",
                "description": f"{skill_name}: {action_name} — {when_desc}",
                "inputSchema": {
                    "type": "object",
                    "properties": properties
                },
                "_source_server": f"skills:{filename}",
                "_source_type": "skill_file"
            }
            tools.append(tool)

        # If no template sections found, create a tool for the whole skill
        if not tools and skill_name:
            tools.append({
                "name": skill_name.lower().replace(" ", "_"),
                "description": f"{skill_name}: {skill_desc}",
                "inputSchema": {"type": "object", "properties": {}},
                "_source_server": f"skills:{filename}",
                "_source_type": "skill_file"
            })

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
        and converts them into the universal ToolDNS format, generates
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

        # Batch embed all descriptions at once (more efficient)
        descriptions = [
            f"{t.get('name', '')}: {t.get('description', '')}"
            for t in raw_tools
        ]
        embeddings = self.embedder.embed_batch(descriptions)

        count = 0
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

            self.db.upsert_tool(
                tool_id=tool_id,
                name=name,
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
                source_info=source_info,
                tags=self._extract_tags(name, tool.get("description", "")),
                embedding=embedding
            )
            count += 1

        return count

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

        Args:
            config: The source configuration dict.

        Returns:
            str: A stable source ID.
        """
        key = f"{config.get('name', '')}:{config.get('type', '')}"
        return hashlib.md5(key.encode()).hexdigest()[:12]
