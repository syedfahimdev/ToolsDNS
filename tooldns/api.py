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

from fastapi import APIRouter, Depends, HTTPException
from tooldns.auth import require_api_key
from tooldns.models import (
    SearchRequest, SearchResponse,
    SourceRequest, SourceResponse, SourceType
)

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

# These get injected by main.py at startup
_search_engine = None
_ingestion_pipeline = None
_database = None


def init_api(search_engine, ingestion_pipeline, database):
    """
    Inject dependencies into the API module.

    Called once at application startup by main.py. Avoids circular
    imports and keeps the module testable.

    Args:
        search_engine: The SearchEngine instance.
        ingestion_pipeline: The IngestionPipeline instance.
        database: The ToolDatabase instance.
    """
    global _search_engine, _ingestion_pipeline, _database
    _search_engine = search_engine
    _ingestion_pipeline = ingestion_pipeline
    _database = database


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


@router.get("/sources")
async def list_sources():
    """
    List all registered sources with their status and tool counts.

    Returns:
        list[dict]: All sources with metadata.
    """
    return _database.get_all_sources()


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

@router.get("/tools")
async def list_tools(source: str = None):
    """
    List all indexed tools, optionally filtered by source.

    Args:
        source: Optional source name to filter by.

    Returns:
        dict: Tool list with count.
    """
    if source:
        tools = _database.get_tools_by_source(source)
    else:
        tools = _database.get_all_tools()

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
    tools = _database.get_all_tools()
    tool = next((t for t in tools if t["id"] == tool_id), None)

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
    tools = _database.get_all_tools()
    tool = next((t for t in tools if t["id"] == tool_id), None)

    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_id}")

    source_info = tool.get("source_info", {})
    source_type = source_info.get("source_type", "")

    # For skills, return the skill content — the LLM executes it
    if "skill" in source_type:
        content = _load_skill_content(tool["name"], source_info)
        return {
            "type": "skill",
            "name": tool["name"],
            "content": content,
            "instruction": "Follow the skill instructions above to complete the task."
        }

    # For MCP tools, proxy the call to the original server
    if "mcp" in source_type or source_type in ("streamableHttp", "sse"):
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
    Forward a tool call to the original MCP server via HTTP.

    Sends a tools/call JSON-RPC request to the MCP server
    that originally provided this tool.

    Args:
        tool: The full tool record from the database.
        arguments: The arguments to pass to the tool.

    Returns:
        dict: The MCP server's response.
    """
    import httpx
    import os

    source_info = tool.get("source_info", {})
    server = source_info.get("server", "")
    original_name = source_info.get("original_name", tool["name"])

    # Look up the server config from registered sources
    sources = _database.get_all_sources()
    server_url = None
    server_headers = {}

    for src in sources:
        config = src.get("config", {})
        # The source config might contain the original server URL
        if "path" in config:
            # MCP config file — need to read it to find the server
            from pathlib import Path
            import json as json_mod

            config_path = Path(os.path.expanduser(config.get("path", "")))
            if config_path.exists():
                raw = json_mod.loads(config_path.read_text())
                config_key = config.get("config_key", "tools.mcpServers")
                mcp_section = raw
                for key in config_key.split("."):
                    mcp_section = mcp_section.get(key, {})

                if server in mcp_section:
                    srv = mcp_section[server]
                    server_url = srv.get("url", "")
                    server_headers = srv.get("headers", {})
                    # Resolve env vars
                    server_url = _resolve_env(server_url)
                    server_headers = {
                        k: _resolve_env(v)
                        for k, v in server_headers.items()
                    }
                    break

    if not server_url:
        raise RuntimeError(
            f"Cannot find server URL for '{server}'. "
            f"The tool's MCP server may not be HTTP-based."
        )

    # Send tools/call request
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **server_headers
    }

    # Get session first
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

    # Send initialized notification
    httpx.post(
        server_url, headers=h,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        timeout=10
    )

    # Call the tool
    resp = httpx.post(
        server_url, headers=h,
        json={
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/call",
            "params": {"name": original_name, "arguments": arguments}
        },
        timeout=60
    )
    resp.raise_for_status()

    # Parse response (could be JSON or SSE)
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
# Ingestion
# -----------------------------------------------------------------------

@router.post("/ingest")
async def refresh_all():
    """
    Re-ingest all registered sources.

    Refreshes the tool index by re-fetching tools from every
    registered source. Use this after adding new tools to an
    MCP server or updating skill files.
    """
    try:
        total = _ingestion_pipeline.ingest_all()
        return {
            "status": "success",
            "total_tools_ingested": total,
            "sources_refreshed": len(_database.get_all_sources())
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
