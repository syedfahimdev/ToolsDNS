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
