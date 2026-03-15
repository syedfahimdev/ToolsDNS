"""
api.py — FastAPI routes for the ToolDNS API.

Exposes four main endpoints:
    POST /v1/search  — Search for tools by natural language query
    POST /v1/sources — Add a new tool source
    GET  /v1/sources — List all registered sources
    GET  /v1/tools   — List all indexed tools
    POST /v1/ingest  — Re-ingest all sources (refresh)
    DELETE /v1/sources/{id} — Remove a source and its tools

Each endpoint validates input via Pydantic models (see models.py)
and requires a valid API key (see auth.py).
"""

import uuid
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from tooldns.auth import require_api_key
from tooldns.models import (
    SearchRequest, SearchResponse,
    SourceRequest, SourceResponse, SourceType,
    RegisterMCPRequest, CreateSkillRequest
)

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
admin_router = APIRouter(prefix="/v1")

# These get injected by main.py at startup
_search_engine = None
_ingestion_pipeline = None
_database = None
_health_monitor = None


def init_api(search_engine, ingestion_pipeline, database, health_monitor=None):
    """
    Inject dependencies into the API module.

    Called once at application startup by main.py. Avoids circular
    imports and keeps the module testable.

    Args:
        search_engine: The SearchEngine instance.
        ingestion_pipeline: The IngestionPipeline instance.
        database: The ToolDatabase instance.
        health_monitor: Optional HealthMonitor instance.
    """
    global _search_engine, _ingestion_pipeline, _database, _health_monitor
    _search_engine = search_engine
    _ingestion_pipeline = ingestion_pipeline
    _database = database
    _health_monitor = health_monitor


# -----------------------------------------------------------------------
# Search
# -----------------------------------------------------------------------

@router.post("/search", response_model=SearchResponse)
async def search_tools(req: SearchRequest):
    """
    Search for tools matching a natural language query.

    This is the core endpoint. Send a description of what you need,
    and get back only the relevant tool schema(s).

    Example:
        POST /v1/search
        {"query": "create a github issue", "top_k": 2}

    Returns:
        SearchResponse with matched tools, confidence scores,
        tokens_saved metric, and search time.
    """
    return _search_engine.search(
        query=req.query,
        top_k=req.top_k,
        threshold=req.threshold
    )


# -----------------------------------------------------------------------
# Sources
# -----------------------------------------------------------------------

@router.post("/sources", response_model=SourceResponse)
async def add_source(req: SourceRequest):
    """
    Register a new tool source and ingest its tools.

    Supports multiple source types:
    - mcp_config: Point to a config file with MCP servers
    - mcp_stdio: A single stdio MCP server
    - mcp_http: A single HTTP MCP server
    - skill_directory: A directory of skill .md files
    - custom: A single custom tool definition

    The source is immediately ingested after registration.
    """
    config = {
        "type": req.type,
        "name": req.name,
        "path": req.path,
        "url": req.url,
        "command": req.command,
        "args": req.args,
        "headers": req.headers,
        "config_key": req.config_key,
        "tool_name": req.tool_name,
        "tool_description": req.tool_description,
        "tool_schema": req.tool_schema,
    }

    try:
        # Re-enable if it was previously deleted
        from tooldns.ingestion import IngestionPipeline
        IngestionPipeline.enable_source(req.name)
        count = _ingestion_pipeline.ingest_source(config)
        sources = _database.get_all_sources()
        source = next(
            (s for s in sources if s["name"] == req.name), None
        )
        return SourceResponse(
            id=source["id"] if source else req.name,
            name=req.name,
            type=req.type,
            tools_count=count,
            status="active",
            last_refreshed=source.get("last_refreshed") if source else None
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _sanitize_source(source: dict, is_admin: bool) -> dict:
    """Strip server-internal paths and config from non-admin responses."""
    if is_admin:
        return source
    safe = {k: v for k, v in source.items() if k not in ("config",)}
    if "config" in source:
        # Only expose type and name — never paths, URLs, headers, or env vars
        cfg = source["config"]
        safe["config"] = {"type": cfg.get("type", ""), "name": cfg.get("name", "")}
    return safe


@router.get("/sources")
async def list_sources(key_info: dict = Depends(require_api_key)):
    """
    List all registered sources with their status and tool counts.

    Admin keys see full config. Sub-keys see name/type/status only.
    """
    sources = _database.get_all_sources()
    is_admin = key_info.get("is_admin", False)
    return [_sanitize_source(s, is_admin) for s in sources]


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str):
    """
    Remove a source and all its indexed tools.

    Args:
        source_id: The source ID to delete.
    """
    deleted = _database.delete_source(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Source not found.")
    return {"status": "deleted", "source_id": source_id}


# -----------------------------------------------------------------------
# Tools
# -----------------------------------------------------------------------

@router.get("/categories")
async def list_categories():
    """
    List all tool categories with counts.

    Returns categories sorted by tool count descending.
    """
    from tooldns.categories import CATEGORIES
    cats = _database.get_categories()
    # Ensure all known categories appear even if count is 0
    present = {c["category"] for c in cats}
    for cat in CATEGORIES:
        if cat not in present:
            cats.append({"category": cat, "count": 0})
    return {"categories": cats, "total_categories": len([c for c in cats if c["count"] > 0])}


@router.get("/tools")
async def list_tools(source: str = None, category: str = None):
    """
    List all indexed tools, optionally filtered by source or category.

    Args:
        source: Optional source name to filter by.
        category: Optional category name to filter by (e.g. "Dev & Code").

    Returns:
        dict: Tool list with count.
    """
    if source:
        tools = _database.get_tools_by_source(source)
    else:
        tools = _database.get_all_tools()

    if category:
        tools = [t for t in tools if (t.get("category") or "Other") == category]

    return {
        "tools": tools,
        "total": len(tools)
    }


@router.get("/tool/{tool_id:path}")
async def get_tool(tool_id: str):
    """
    Get full details for a specific tool.

    Returns the tool's schema, description, how_to_call instructions,
    and for skills, the full skill file content that the LLM needs.

    This is the key endpoint for the execution flow:
        1. LLM calls /v1/search → finds the right tool
        2. LLM calls /v1/tool/{id} → gets full schema + instructions
        3. LLM executes the tool via original MCP server or skill content

    Args:
        tool_id: The tool's unique identifier.

    Returns:
        dict: Full tool details including skill_content if applicable.
    """
    from pathlib import Path
    import json as json_mod

    # Find the tool in the database
    tool = _database.get_tool_by_id(tool_id)

    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_id}")

    source_info = tool.get("source_info", {})
    result = {
        "id": tool["id"],
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool.get("input_schema", {}),
        "source": source_info.get("source_name", "unknown"),
        "source_type": source_info.get("source_type", "unknown"),
        "how_to_call": _search_engine._build_call_instructions(source_info),
        "tags": tool.get("tags", []),
    }

    # For skills, include the actual skill file content
    if source_info.get("source_type") in ("skill", "skill_directory"):
        skill_content = _load_skill_content(tool["name"], source_info)
        if skill_content:
            result["skill_content"] = skill_content

    return result


def _load_skill_content(tool_name: str, source_info: dict) -> str:
    """
    Load the full skill file content for a skill-type tool.

    Searches through known skill directories for the matching
    SKILL.md or .md file.

    Args:
        tool_name: The skill/tool name.
        source_info: The tool's source metadata.

    Returns:
        str: The skill file content, or empty string if not found.
    """
    from pathlib import Path
    from tooldns.config import TOOLDNS_HOME
    import json as json_mod

    # Build list of skill directories to search
    skill_dirs = []

    # Check config.json for skillPaths
    config_file = TOOLDNS_HOME / "config.json"
    if config_file.exists():
        try:
            config = json_mod.loads(config_file.read_text())
            for sp in config.get("skillPaths", []):
                p = Path(sp).expanduser()
                if p.exists():
                    skill_dirs.append(p)
        except Exception:
            pass

    # Also check ~/.tooldns/skills/
    local_skills = TOOLDNS_HOME / "skills"
    if local_skills.exists():
        skill_dirs.append(local_skills)

    # Search for the skill file
    for skill_dir in skill_dirs:
        # Pattern 1: folder/SKILL.md
        for item in skill_dir.iterdir():
            if item.is_dir():
                skill_file = item / "SKILL.md"
                if skill_file.exists():
                    # Check if this matches by name
                    content = skill_file.read_text(encoding="utf-8")
                    if _skill_name_matches(content, item.name, tool_name):
                        return content

            # Pattern 2: flat .md file
            elif item.is_file() and item.suffix == ".md" and item.name != "_index.md":
                content = item.read_text(encoding="utf-8")
                if _skill_name_matches(content, item.stem, tool_name):
                    return content

    return ""


def _skill_name_matches(content: str, filename: str, target_name: str) -> bool:
    """Check if a skill file matches by name in frontmatter or filename."""
    target_lower = target_name.lower().replace("_", "-").replace(" ", "-")
    file_lower = filename.lower()

    if file_lower == target_lower:
        return True

    # Check YAML frontmatter name field
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("'\"")
                    if name.lower() == target_lower:
                        return True
    return False


# -----------------------------------------------------------------------
# Tool Execution Proxy
# -----------------------------------------------------------------------

@router.post("/call")
async def call_tool(req: dict):
    """
    Proxy a tool call to the original MCP server.

    This is the execution bridge — the LLM sends the tool name
    and arguments here, and ToolDNS forwards the call to the
    correct MCP server.

    Request body:
        {
            "tool_id": "nanobot__GMAIL_SEND_EMAIL",
            "arguments": {"to": "john@example.com", "body": "Hello"}
        }

    Returns:
        dict: The tool's execution result from the MCP server.
    """
    tool_id = req.get("tool_id", "")
    arguments = req.get("arguments", {})

    if not tool_id:
        raise HTTPException(status_code=400, detail="tool_id is required")

    # Find the tool
    tool = _database.get_tool_by_id(tool_id)

    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_id}")

    source_info = tool.get("source_info", {})
    source_type = source_info.get("source_type", "")

    # For skills, return the skill content — the LLM executes it
    # Use exact match so skill_tool_stdio/skill_tool_script fall through to MCP execution
    _SKILL_CONTENT_TYPES = {"skill", "skill_directory", "skill_file"}
    if source_type in _SKILL_CONTENT_TYPES:
        content = _load_skill_content(tool["name"], source_info)
        return {
            "type": "skill",
            "name": tool["name"],
            "content": content,
            "instruction": "Follow the skill instructions above to complete the task."
        }

    # For MCP tools and skill tool scripts, proxy the call to the server/script
    if "mcp" in source_type or source_type in ("streamableHttp", "sse", "skill_tool_stdio", "skill_tool_script"):
        try:
            result = _proxy_mcp_call(tool, arguments)
            return {"type": "mcp_result", "result": result}
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"MCP call failed: {e}"
            )

    raise HTTPException(
        status_code=400,
        detail=f"Execution not supported for source type: {source_type}"
    )


def _proxy_mcp_call(tool: dict, arguments: dict) -> dict:
    """
    Forward a tool call to the original MCP server.

    Supports both stdio and HTTP transports. Uses transport info
    stored in source_info during ingestion to route the call correctly.

    Args:
        tool: The full tool record from the database.
        arguments: The arguments to pass to the tool.

    Returns:
        dict: The MCP server's response.
    """
    from tooldns.fetcher import MCPFetcher

    source_info = tool.get("source_info", {})
    original_name = source_info.get("original_name", tool["name"])
    source_type = source_info.get("source_type", "")

    fetcher = MCPFetcher()

    # --- stdio execution (spawn process on demand) ---
    if source_type == "stdio" or source_info.get("command"):
        command = source_info.get("command")
        args = source_info.get("args", [])

        if not command:
            # Fall back: look it up from the registered source config
            command, args = _lookup_stdio_config(source_info, _database)

        if not command:
            raise RuntimeError(
                f"Cannot execute stdio tool '{original_name}': "
                f"command not found in source_info. Re-ingest the source to fix this."
            )

        return fetcher.call_stdio(command, args, original_name, arguments)

    # --- HTTP execution ---
    server_url = source_info.get("url", "")
    server_headers = source_info.get("headers", {})

    if not server_url:
        # Fall back: look it up from the registered source config
        server_url, server_headers = _lookup_http_config(source_info, _database)

    if not server_url:
        raise RuntimeError(
            f"Cannot execute tool '{original_name}': no URL or command found. "
            f"Source type '{source_type}' — re-ingest the source to fix this."
        )

    return _http_tool_call(server_url, server_headers, original_name, arguments)


def _lookup_stdio_config(source_info: dict, database) -> tuple:
    """Look up command+args from registered source configs for a stdio tool."""
    import os
    from pathlib import Path

    server = source_info.get("server", "")
    sources = database.get_all_sources()

    for src in sources:
        config = src.get("config", {})
        if "path" in config:
            config_path = Path(os.path.expanduser(config.get("path", "")))
            if not config_path.exists():
                continue
            try:
                import json as json_mod
                raw = json_mod.loads(config_path.read_text())
                config_key = config.get("config_key", "tools.mcpServers")
                section = raw
                for key in config_key.split("."):
                    section = section.get(key, {})
                if server in section:
                    srv = section[server]
                    if srv.get("command"):
                        return srv["command"], srv.get("args", [])
            except Exception:
                continue

    return None, []


def _lookup_http_config(source_info: dict, database) -> tuple:
    """Look up URL+headers from registered source configs for an HTTP tool."""
    import os
    from pathlib import Path

    server = source_info.get("server", "")
    sources = database.get_all_sources()

    for src in sources:
        config = src.get("config", {})
        if "path" in config:
            config_path = Path(os.path.expanduser(config.get("path", "")))
            if not config_path.exists():
                continue
            try:
                import json as json_mod
                raw = json_mod.loads(config_path.read_text())
                config_key = config.get("config_key", "tools.mcpServers")
                section = raw
                for key in config_key.split("."):
                    section = section.get(key, {})
                if server in section:
                    srv = section[server]
                    url = _resolve_env(srv.get("url", ""))
                    headers = {k: _resolve_env(v) for k, v in srv.get("headers", {}).items()}
                    if url:
                        return url, headers
            except Exception:
                continue

    return None, {}


def _http_tool_call(server_url: str, server_headers: dict,
                    tool_name: str, arguments: dict) -> dict:
    """Send a tools/call request to an HTTP MCP server."""
    import httpx

    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **server_headers
    }

    init_resp = httpx.post(
        server_url, headers=h,
        json={
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "tooldns-proxy", "version": "1.0.0"}
            }
        },
        timeout=30
    )
    session_id = init_resp.headers.get("mcp-session-id")
    if session_id:
        h["mcp-session-id"] = session_id

    httpx.post(
        server_url, headers=h,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        timeout=10
    )

    resp = httpx.post(
        server_url, headers=h,
        json={
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments}
        },
        timeout=60
    )
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        import json as json_mod
        for line in resp.text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if data:
                    try:
                        parsed = json_mod.loads(data)
                        return parsed.get("result", parsed)
                    except Exception:
                        continue
        return {"raw": resp.text}
    else:
        data = resp.json()
        return data.get("result", data)


def _resolve_env(val):
    """Resolve ${ENV_VAR} references in strings."""
    import os
    import re
    if isinstance(val, str):
        def replacer(m):
            return os.environ.get(m.group(1), "")
        return re.sub(r'\$\{(\w+)\}', replacer, val)
    return val


# -----------------------------------------------------------------------
# Agent-facing: Register MCP server
# -----------------------------------------------------------------------

@router.post("/register-mcp")
async def register_mcp(req: RegisterMCPRequest):
    """
    Register a new MCP server into ToolDNS — callable by AI agents.

    Saves env vars, updates ~/.tooldns/config.json, and optionally
    ingests the server's tools immediately. No interactive prompts.

    Example (stdio):
        POST /v1/register-mcp
        {
            "name": "github",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env_vars": {"GITHUB_TOKEN": "ghp_xxxx"},
            "ingest": true
        }

    Example (HTTP):
        POST /v1/register-mcp
        {
            "name": "composio",
            "url": "https://mcp.composio.dev/...",
            "headers": {"x-api-key": "..."},
            "ingest": true
        }
    """
    import os
    import json as json_mod
    from pathlib import Path
    from tooldns.config import TOOLDNS_HOME

    if not req.name:
        raise HTTPException(status_code=400, detail="name is required")
    if not req.command and not req.url:
        raise HTTPException(status_code=400, detail="either command or url is required")

    # 1. Save env vars to ~/.tooldns/.env
    saved_vars = []
    if req.env_vars:
        env_path = TOOLDNS_HOME / ".env"
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()
        existing.update(req.env_vars)
        env_path.write_text(
            "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n"
        )
        # Export for this process so ingestion works immediately
        for k, v in req.env_vars.items():
            os.environ[k] = v
        saved_vars = list(req.env_vars.keys())

    # 2. Add server to ~/.tooldns/config.json
    config_file = TOOLDNS_HOME / "config.json"
    config_data = {}
    if config_file.exists():
        try:
            config_data = json_mod.loads(config_file.read_text())
        except Exception:
            pass

    mcp_servers = config_data.setdefault("mcpServers", {})

    if req.url:
        entry = {"type": "streamableHttp", "url": req.url}
        if req.headers:
            entry["headers"] = req.headers
    else:
        # Replace literal env var values in args with ${VAR} references
        safe_args = list(req.args or [])
        if req.env_vars:
            for i, arg in enumerate(safe_args):
                for var_name, var_value in req.env_vars.items():
                    if var_value and var_value in arg:
                        safe_args[i] = arg.replace(var_value, f"${{{var_name}}}")
        entry = {"command": req.command, "args": safe_args}

    mcp_servers[req.name] = entry
    config_file.write_text(json_mod.dumps(config_data, indent=2))

    # 3. Ingest tools
    tools_count = 0
    ingest_error = None
    if req.ingest:
        try:
            source_config = {
                "type": SourceType.MCP_CONFIG,
                "name": f"agent-{req.name}",
                "path": str(config_file),
                "config_key": "mcpServers",
                "skip_servers": [s for s in mcp_servers if s != req.name],
            }
            tools_count = _ingestion_pipeline.ingest_source(source_config)
        except Exception as e:
            ingest_error = str(e)

    return {
        "status": "registered",
        "name": req.name,
        "transport": "http" if req.url else "stdio",
        "env_vars_saved": saved_vars,
        "config_file": str(config_file),
        "tools_indexed": tools_count,
        "ingest_error": ingest_error,
    }


# -----------------------------------------------------------------------
# Agent-facing: List + Create skill
# -----------------------------------------------------------------------

@router.get("/skills")
async def list_skills():
    """List all skills in ~/.tooldns/skills/ with name and description."""
    from tooldns.config import TOOLDNS_HOME
    import re as _re
    import yaml as _yaml

    skills_dir = TOOLDNS_HOME / "skills"
    skills = []

    if skills_dir.exists():
        for item in sorted(skills_dir.iterdir()):
            if item.is_dir():
                skill_file = item / "SKILL.md"
                if not skill_file.exists():
                    continue
            elif item.is_file() and item.suffix == ".md":
                skill_file = item
            else:
                continue
            try:
                content = skill_file.read_text(encoding="utf-8")
                name = item.name
                description = ""
                # Simple line-by-line frontmatter parse (handles malformed YAML)
                fm_match = _re.match(r"^---\s*\n(.*?)\n---", content, _re.DOTALL)
                if fm_match:
                    for line in fm_match.group(1).splitlines():
                        m = _re.match(r'^(name|description)\s*:\s*"?(.+?)"?\s*$', line)
                        if m:
                            if m.group(1) == "name":
                                name = m.group(2).strip('"')
                            elif m.group(1) == "description":
                                description = m.group(2).strip('"')
                skills.append({"name": name, "description": description})
            except Exception:
                pass

    return {"skills": skills, "total": len(skills)}


@router.post("/skills")
async def create_skill(req: CreateSkillRequest):
    """
    Create a new skill file — callable by AI agents.

    Writes a SKILL.md file to the ToolDNS skills directory (or a
    specified path) and re-indexes skills immediately. Agents can
    compose the markdown content themselves and POST it here.

    Example:
        POST /v1/skills
        {
            "name": "send-report",
            "description": "Sends a weekly report via email",
            "content": "---\\nname: send-report\\n...\\n",
            "ingest": true
        }
    """
    import os
    from pathlib import Path
    from tooldns.config import TOOLDNS_HOME

    if not req.name:
        raise HTTPException(status_code=400, detail="name is required")
    if not req.content:
        raise HTTPException(status_code=400, detail="content is required")

    # Security: validate name — only safe filename chars, no path traversal
    import re as _re
    if not _re.match(r'^[a-zA-Z0-9_\-]+$', req.name):
        raise HTTPException(status_code=400, detail="name must contain only letters, numbers, hyphens, underscores")
    if len(req.content) > 500_000:
        raise HTTPException(status_code=400, detail="content too large (max 500KB)")

    # Determine target directory
    if req.skill_path:
        # Validate skill_path stays within allowed base directories
        allowed_bases = [str(TOOLDNS_HOME), os.path.expanduser("~/.nanobot"), os.path.expanduser("~/.openclaw")]
        expanded = os.path.realpath(os.path.expanduser(req.skill_path))
        if not any(expanded.startswith(b) for b in allowed_bases):
            raise HTTPException(status_code=400, detail="skill_path must be within ~/.tooldns, ~/.nanobot, or ~/.openclaw")
        skill_dir = Path(expanded)
    else:
        skill_dir = TOOLDNS_HOME / "skills"

    skill_dir.mkdir(parents=True, exist_ok=True)

    # Write skill file (support both folder/SKILL.md and flat .md)
    skill_folder = skill_dir / req.name
    # Security: ensure resolved path is still within skill_dir
    resolved_folder = Path(os.path.realpath(str(skill_dir / req.name)))
    if not str(resolved_folder).startswith(str(skill_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid skill name")
    skill_folder.mkdir(exist_ok=True)
    skill_file = skill_folder / "SKILL.md"

    # Ensure frontmatter has name + description
    content = req.content
    if not content.startswith("---"):
        content = f"---\nname: {req.name}\ndescription: {req.description}\n---\n\n{content}"

    skill_file.write_text(content, encoding="utf-8")

    # Re-ingest skills
    tools_count = 0
    if req.ingest:
        try:
            source_config = {
                "type": SourceType.SKILL_DIRECTORY,
                "name": f"skills-{req.name}",
                "path": str(skill_dir),
            }
            tools_count = _ingestion_pipeline.ingest_source(source_config)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Skill written but indexing failed: {e}")

    return {
        "status": "created",
        "name": req.name,
        "file": str(skill_file),
        "tools_indexed": tools_count,
    }


# -----------------------------------------------------------------------
# Skill read / update (agent-safe editing)
# -----------------------------------------------------------------------

@router.get("/skills/{skill_name}")
async def read_skill(skill_name: str):
    """
    Read a skill's SKILL.md content and list any tool scripts inside the folder.

    Agents can call this before editing to see what's currently there.
    Returns skill content + any .py tool scripts found in the folder.
    """
    import re as _re
    from pathlib import Path
    from tooldns.config import TOOLDNS_HOME

    if not _re.match(r'^[a-zA-Z0-9_\-]+$', skill_name):
        raise HTTPException(status_code=400, detail="Invalid skill name")

    skill_dir = TOOLDNS_HOME / "skills" / skill_name
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.exists():
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_name}")

    content = skill_file.read_text(encoding="utf-8")

    # List tool scripts
    tool_scripts = []
    for script in sorted(skill_dir.glob("*.py")):
        tool_scripts.append({
            "name": script.name,
            "size": script.stat().st_size,
            "content": script.read_text(encoding="utf-8") if script.stat().st_size < 100_000 else None,
        })

    return {
        "name": skill_name,
        "content": content,
        "file": str(skill_file),
        "tool_scripts": tool_scripts,
    }


@router.put("/skills/{skill_name}")
async def update_skill(skill_name: str, req: dict):
    """
    Safely update a skill's SKILL.md and/or a tool script.

    Always creates a .bak backup before writing. Re-indexes immediately.
    Validates names to prevent path traversal.

    Request body:
        {
            "content": "new SKILL.md content",
            "script_name": "tool.py",       (optional)
            "script_content": "..."          (optional)
        }
    """
    import re as _re
    import shutil
    from pathlib import Path
    from tooldns.config import TOOLDNS_HOME

    if not _re.match(r'^[a-zA-Z0-9_\-]+$', skill_name):
        raise HTTPException(status_code=400, detail="Invalid skill name")

    content = req.get("content", "")
    script_name = req.get("script_name")
    script_content = req.get("script_content")

    skill_dir = TOOLDNS_HOME / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_name}")

    if len(content) > 500_000:
        raise HTTPException(status_code=400, detail="Content too large (max 500KB)")

    updated_files = []

    # Update SKILL.md
    if content:
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            shutil.copy2(skill_file, skill_dir / "SKILL.md.bak")
        if not content.startswith("---"):
            content = f"---\nname: {skill_name}\n---\n\n{content}"
        skill_file.write_text(content, encoding="utf-8")
        updated_files.append("SKILL.md")

    # Update tool script
    if script_name and script_content:
        if not _re.match(r'^[a-zA-Z0-9_\-]+\.py$', script_name):
            raise HTTPException(status_code=400, detail="Invalid script name — must end in .py")
        if len(script_content) > 500_000:
            raise HTTPException(status_code=400, detail="Script too large (max 500KB)")

        script_file = skill_dir / script_name
        # Path traversal check
        if not str(script_file.resolve()).startswith(str(skill_dir.resolve())):
            raise HTTPException(status_code=400, detail="Invalid script path")

        if script_file.exists():
            shutil.copy2(script_file, skill_dir / f"{script_name}.bak")
        script_file.write_text(script_content, encoding="utf-8")
        updated_files.append(script_name)

    # Re-index
    tools_count = 0
    try:
        source_config = {
            "type": SourceType.SKILL_DIRECTORY,
            "name": f"skills-{skill_name}",
            "path": str(TOOLDNS_HOME / "skills"),
        }
        tools_count = _ingestion_pipeline.ingest_source(source_config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Files updated but re-index failed: {e}")

    return {
        "status": "updated",
        "name": skill_name,
        "updated_files": updated_files,
        "tools_indexed": tools_count,
    }


# -----------------------------------------------------------------------
# Ingestion
# -----------------------------------------------------------------------

@router.post("/ingest")
async def refresh_all():
    """
    Re-ingest all registered sources (async).

    Returns a job_id immediately. Poll GET /v1/ingest/{job_id} for status.
    Ingestion runs in a background thread so the server stays responsive.
    """
    job_id = str(uuid.uuid4())
    _database.create_job(job_id)
    asyncio.create_task(_run_ingest_job(job_id))
    return {"job_id": job_id, "status": "queued"}


async def _run_ingest_job(job_id: str):
    """Background coroutine that runs ingestion in a thread pool."""
    _database.update_job(job_id, "running")
    try:
        loop = asyncio.get_event_loop()
        total = await loop.run_in_executor(None, _ingestion_pipeline.ingest_all)
        _database.update_job(job_id, "completed", total_tools=total)
    except Exception as e:
        _database.update_job(job_id, "failed", error=str(e))


@router.get("/ingest/{job_id}")
async def get_ingest_job(job_id: str):
    """
    Get the status of an async ingestion job.

    Args:
        job_id: The job ID returned by POST /v1/ingest.

    Returns:
        dict: Job status, total_tools, error (if any).
    """
    job = _database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# -----------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------

@router.get("/health")
async def tool_health():
    """
    Get health status for all sources and tools.

    Returns counts by status (healthy/degraded/down/unknown) and
    per-source health details. Updated every 60 seconds by the
    background health monitor.
    """
    return _database.get_health_summary()


@router.post("/health/check")
async def trigger_health_check():
    """
    Trigger an immediate health check (async).

    Runs the health monitor in the background and returns immediately.
    """
    if _health_monitor:
        asyncio.create_task(_health_monitor.check_all())
        return {"status": "health check triggered"}
    return {"status": "health monitor not configured"}


# -----------------------------------------------------------------------
# Marketplace
# -----------------------------------------------------------------------

@router.get("/marketplace")
async def list_marketplace(query: str = "", limit: int = 20):
    """
    Browse the MCP server marketplace.

    Returns the curated list of popular servers merged with live results
    from the Smithery registry. Curated entries always take priority;
    dynamic results fill in additional servers not already listed.

    Args:
        query: Optional search term forwarded to Smithery.
        limit: Maximum number of dynamic servers to fetch from Smithery (default 20).

    Returns:
        dict: Combined server list with total count.
    """
    from tooldns.marketplace import get_dynamic_servers
    servers = get_dynamic_servers(query=query, limit=limit)
    return {"servers": servers, "total": len(servers)}


# -----------------------------------------------------------------------
# Discover
# -----------------------------------------------------------------------

@router.post("/discover")
async def discover_source(req: dict):
    """
    Auto-discover an MCP server from any URL.

    Accepts a URL pointing to:
      - Smithery.ai server page  (smithery.ai/server/...)
      - npm package page         (npmjs.com/package/...)
      - GitHub repository        (github.com/user/repo)
      - Direct HTTP MCP endpoint (any https://... URL)

    ToolDNS detects the type, generates the source config, and optionally
    ingests it immediately.

    Request body:
        {"url": "https://smithery.ai/server/@modelcontextprotocol/server-github", "ingest": true}

    Returns:
        dict: Detected config + ingestion result (if ingest=true).
    """
    from tooldns.discover import discover_from_url

    url = req.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    auto_ingest = req.get("ingest", False)
    result = discover_from_url(url)

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    response = {
        "url": url,
        "detected_type": result.get("detected_type"),
        "message": result.get("message"),
        "source_config": result.get("source_config"),
    }

    if auto_ingest and result.get("source_config"):
        try:
            count = _ingestion_pipeline.ingest_source(result["source_config"])
            response["ingested"] = True
            response["tools_count"] = count
        except Exception as e:
            response["ingested"] = False
            response["ingest_error"] = str(e)

    return response


# -----------------------------------------------------------------------
# API Key Management (admin only)
# -----------------------------------------------------------------------

def _require_admin(key_info: dict = Depends(require_api_key)):
    if not key_info.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin key required")
    return key_info


@admin_router.get("/api-keys")
async def list_api_keys(_: dict = Depends(_require_admin)):
    """List all sub-keys (admin only)."""
    keys = _database.get_all_api_keys()
    return {"keys": keys, "total": len(keys)}


@admin_router.post("/api-keys")
async def create_api_key(
    body: dict,
    _: dict = Depends(_require_admin),
):
    """Create a new sub-key (admin only).

    Body: { "name": "acme-corp", "label": "Acme Corp", "plan": "pro", "monthly_limit": 1000 }
    """
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    key = _database.create_api_key(
        name=name,
        label=body.get("label", ""),
        plan=body.get("plan", "free"),
        monthly_limit=int(body.get("monthly_limit", 1000)),
    )
    return {"key": key, "name": name}


@admin_router.post("/api-keys/{key}/revoke")
async def revoke_api_key(key: str, _: dict = Depends(_require_admin)):
    """Revoke a sub-key (admin only)."""
    _database.revoke_api_key(key)
    return {"ok": True}


@admin_router.post("/api-keys/{key}/reset")
async def reset_api_key(key: str, _: dict = Depends(_require_admin)):
    """Reset monthly usage counter for a sub-key (admin only)."""
    _database.reset_key_monthly_count(key)
    return {"ok": True}


@admin_router.delete("/api-keys/{key}")
async def delete_api_key(key: str, _: dict = Depends(_require_admin)):
    """Permanently delete a sub-key (admin only)."""
    _database.delete_api_key(key)
    return {"ok": True}
