"""
api.py — FastAPI routes for the ToolsDNS API.

Endpoints:
    POST /v1/search           — Search for tools by natural language query
    POST /v1/search/batch     — Batch search: multiple queries, one HTTP call
    POST /v1/sources          — Add a new tool source
    GET  /v1/sources          — List all registered sources
    GET  /v1/tools            — List all indexed tools
    POST /v1/ingest           — Re-ingest all sources (refresh)
    DELETE /v1/sources/{id}   — Remove a source and its tools

    # Multi-agent token saving
    POST   /v1/sessions             — Create an agent session for schema dedup
    GET    /v1/sessions/{id}        — Get session stats
    DELETE /v1/sessions/{id}        — End a session
    POST   /v1/profiles             — Create a tool profile (scoped tool subset)
    GET    /v1/profiles             — List all profiles
    GET    /v1/profiles/{name}      — Get a profile + its matched tool count
    DELETE /v1/profiles/{name}      — Delete a profile
    GET    /v1/cost-report          — Token savings & cost report across all agents

Each endpoint validates input via Pydantic models (see models.py)
and requires a valid API key (see auth.py).
"""

import os
import uuid
import fnmatch
import json as _json
import threading
import time
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from tooldns.config import settings, TOOLDNS_HOME
from tooldns.auth import require_api_key
from tooldns.models import (
    SearchRequest, SearchResponse,
    BatchSearchRequest, BatchSearchResponse,
    CreateSessionRequest, SessionInfo,
    CreateProfileRequest, ProfileInfo,
    SourceRequest, SourceResponse, SourceType,
    RegisterMCPRequest, CreateSkillRequest
)

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
admin_router = APIRouter(prefix="/v1")

# ---------------------------------------------------------------------------
# In-memory session store (thread-safe)
# ---------------------------------------------------------------------------
# Sessions track which tool schemas have been sent to an agent so we never
# resend the same schema twice in a session — saves significant tokens.

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _cleanup_sessions() -> None:
    """Evict expired sessions. Called lazily on every session access."""
    now = datetime.utcnow()
    with _sessions_lock:
        expired = [sid for sid, s in _sessions.items() if s["expires_at"] < now]
        for sid in expired:
            del _sessions[sid]


def _get_session(session_id: str) -> dict | None:
    _cleanup_sessions()
    with _sessions_lock:
        return _sessions.get(session_id)


def _update_session(session_id: str, new_tool_ids: list[str], dedup_tokens: int) -> None:
    """Add newly-seen tool IDs and accumulate dedup savings to a session."""
    with _sessions_lock:
        s = _sessions.get(session_id)
        if s:
            s["seen_tool_ids"].update(new_tool_ids)
            s["tokens_saved_by_dedup"] += dedup_tokens


# ---------------------------------------------------------------------------
# Profile store (in-memory + persisted to ~/.tooldns/profiles.json)
# ---------------------------------------------------------------------------

_profiles: dict[str, dict] = {}
_profiles_lock = threading.Lock()
_PROFILES_FILE = TOOLDNS_HOME / "profiles.json"


def _load_profiles() -> None:
    """Load profiles from disk into memory at startup."""
    if _PROFILES_FILE.exists():
        try:
            data = _json.loads(_PROFILES_FILE.read_text())
            with _profiles_lock:
                _profiles.update(data)
        except Exception:
            pass


def _save_profiles() -> None:
    """Persist the in-memory profile dict to disk."""
    TOOLDNS_HOME.mkdir(parents=True, exist_ok=True)
    with _profiles_lock:
        _PROFILES_FILE.write_text(_json.dumps(_profiles, indent=2, default=str))


def _resolve_profile_tool_ids(profile_name: str) -> set[str] | None:
    """
    Resolve a profile name to the set of tool IDs it allows.

    Returns None if profile not found or has no restrictions (= all tools).
    Uses fnmatch glob matching on tool names (e.g. "GMAIL_*").
    """
    with _profiles_lock:
        profile = _profiles.get(profile_name)
    if not profile:
        return None

    patterns = profile.get("tool_patterns", [])
    pinned = set(profile.get("pinned_tool_ids", []))

    if not patterns and not pinned:
        return None  # No restrictions — treat as unrestricted

    all_tools = _database.get_all_tools() if _database else []
    allowed = set(pinned)
    for tool in all_tools:
        tool_name = tool.get("name", "")
        tool_id = tool.get("id", "")
        for pattern in patterns:
            if fnmatch.fnmatch(tool_name, pattern) or fnmatch.fnmatch(tool_id, pattern):
                allowed.add(tool_id)
                break

    return allowed if allowed else None

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
    # Load persisted profiles from disk
    _load_profiles()


# -----------------------------------------------------------------------
# Search
# -----------------------------------------------------------------------

@router.post("/search", response_model=SearchResponse)
def search_tools(req: SearchRequest, auth: dict = Depends(require_api_key)):
    """
    Search for tools matching a natural language query.

    This is the core endpoint. Send a description of what you need,
    and get back only the relevant tool schema(s).

    Multi-agent token saving options:
    - minimal=true  → strip schemas to required fields only (~70% token reduction)
    - session_id    → skip tools already seen in this session (schema dedup)
    - profile       → scope search to a named tool subset (faster + more accurate)

    Example:
        POST /v1/search
        {"query": "create a github issue", "top_k": 2, "minimal": true, "session_id": "abc123"}

    Returns:
        SearchResponse with matched tools, confidence scores,
        tokens_saved, tokens_saved_by_dedup, and search time.
    """
    # Resolve profile → allowed tool IDs
    allowed_tool_ids = None
    profile_name = req.profile
    if profile_name:
        allowed_tool_ids = _resolve_profile_tool_ids(profile_name)

    # Resolve session → seen tool IDs for dedup
    session = None
    seen_tool_ids = None
    if req.session_id:
        session = _get_session(req.session_id)
        if session:
            seen_tool_ids = set(session["seen_tool_ids"])
        else:
            raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")

    response = _search_engine.search(
        query=req.query,
        top_k=req.top_k,
        threshold=req.threshold,
        api_key=auth.get("key", ""),
        minimal=req.minimal,
        allowed_tool_ids=allowed_tool_ids,
        seen_tool_ids=seen_tool_ids,
    )

    # Update session with newly seen tools
    if session and req.session_id:
        new_ids = [r.id for r in response.results if not r.already_seen]
        _update_session(req.session_id, new_ids, response.tokens_saved_by_dedup)
        with _sessions_lock:
            s = _sessions.get(req.session_id)
            response.session_tool_count = len(s["seen_tool_ids"]) if s else 0

    if profile_name:
        response.profile_active = profile_name

    return response


# -----------------------------------------------------------------------
# Batch Search
# -----------------------------------------------------------------------

@router.post("/search/batch", response_model=BatchSearchResponse)
def batch_search_tools(req: BatchSearchRequest, auth: dict = Depends(require_api_key)):
    """
    Execute multiple tool searches in a single HTTP call.

    Critical for multi-agent systems — instead of 16 agents each making
    a separate search request, batch all queries into one call.

    Benefits:
    - Single HTTP round trip regardless of query count
    - Shared session_id enables cross-query schema dedup (tool returned
      for query 1 won't have its schema resent for query 4)
    - Shared profile scopes all queries to the same tool subset
    - Total tokens_saved reported across the entire batch

    Example:
        POST /v1/search/batch
        {
            "queries": [
                {"query": "send gmail email", "top_k": 1},
                {"query": "create github issue", "top_k": 1},
                {"query": "upload to google drive", "top_k": 1}
            ],
            "minimal": true,
            "session_id": "agent-session-abc"
        }
    """
    import time as _time

    # Resolve profile once for the entire batch
    allowed_tool_ids = None
    if req.profile:
        allowed_tool_ids = _resolve_profile_tool_ids(req.profile)

    # Resolve session once — seen_tool_ids is shared across all queries
    session = None
    seen_tool_ids = None
    if req.session_id:
        session = _get_session(req.session_id)
        if session:
            seen_tool_ids = set(session["seen_tool_ids"])
        else:
            raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")

    batch_start = _time.time()
    results = []
    total_tokens_saved = 0
    total_dedup_savings = 0
    total_sequential_ms = 0.0

    for item in req.queries:
        # Use the live seen_tool_ids (updated after each query so dedup works across queries)
        resp = _search_engine.search(
            query=item.query,
            top_k=item.top_k,
            threshold=item.threshold,
            api_key=auth.get("key", ""),
            minimal=req.minimal,
            allowed_tool_ids=allowed_tool_ids,
            seen_tool_ids=seen_tool_ids,
        )

        # Update shared seen_tool_ids so next query in batch benefits from dedup
        if seen_tool_ids is not None:
            new_ids = [r.id for r in resp.results if not r.already_seen]
            seen_tool_ids.update(new_ids)

        if req.profile:
            resp.profile_active = req.profile

        total_tokens_saved += resp.tokens_saved
        total_dedup_savings += resp.tokens_saved_by_dedup
        total_sequential_ms += resp.search_time_ms
        results.append(resp)

    batch_time_ms = (_time.time() - batch_start) * 1000

    # Flush updated seen_tool_ids back to the session store
    if session and req.session_id and seen_tool_ids is not None:
        new_all_ids = list(seen_tool_ids - set(session["seen_tool_ids"]))
        _update_session(req.session_id, new_all_ids, total_dedup_savings)

    return BatchSearchResponse(
        results=results,
        total_queries=len(req.queries),
        total_tokens_saved=total_tokens_saved,
        total_dedup_savings=total_dedup_savings,
        batch_time_ms=round(batch_time_ms, 2),
        vs_sequential_ms=round(total_sequential_ms, 2),
    )


# -----------------------------------------------------------------------
# Agent Sessions
# -----------------------------------------------------------------------

@router.post("/sessions", response_model=SessionInfo)
def create_session(req: CreateSessionRequest):
    """
    Create an agent session for schema dedup tracking.

    Once created, pass the returned session_id in search requests.
    ToolsDNS will track which tool schemas were sent to this agent
    and skip resending them — saving tokens on every repeated search.

    In multi-agent setups:
    - Each agent gets its own session (intra-agent dedup)
    - OR, agents on the same task share a session (inter-agent dedup)
      by setting shared=true and distributing the session_id

    Example:
        POST /v1/sessions
        {"agent_id": "email-agent-1", "profile": "email-agent", "ttl_seconds": 3600}

        → {"session_id": "sess_abc123", "expires_at": "..."}

        Then in searches:
        POST /v1/search
        {"query": "send email", "session_id": "sess_abc123"}
    """
    _cleanup_sessions()
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=req.ttl_seconds)

    session_data = {
        "session_id": session_id,
        "agent_id": req.agent_id,
        "profile": req.profile,
        "shared": req.shared,
        "seen_tool_ids": set(),
        "tokens_saved_by_dedup": 0,
        "created_at": now,
        "expires_at": expires_at,
    }
    with _sessions_lock:
        _sessions[session_id] = session_data

    return SessionInfo(
        session_id=session_id,
        agent_id=req.agent_id,
        profile=req.profile,
        shared=req.shared,
        tools_seen=0,
        tokens_saved_by_dedup=0,
        created_at=now,
        expires_at=expires_at,
    )


@router.get("/sessions/{session_id}", response_model=SessionInfo)
def get_session(session_id: str):
    """
    Get current stats for an agent session.

    Returns how many unique tool schemas have been sent to this agent
    and how many tokens have been saved by not resending duplicates.
    """
    session = _get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found or expired: {session_id}")

    return SessionInfo(
        session_id=session["session_id"],
        agent_id=session.get("agent_id", ""),
        profile=session.get("profile", ""),
        shared=session.get("shared", False),
        tools_seen=len(session["seen_tool_ids"]),
        tokens_saved_by_dedup=session["tokens_saved_by_dedup"],
        created_at=session["created_at"],
        expires_at=session["expires_at"],
    )


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """End an agent session and clear its dedup state."""
    with _sessions_lock:
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        session = _sessions.pop(session_id)

    return {
        "status": "deleted",
        "session_id": session_id,
        "tools_seen": len(session["seen_tool_ids"]),
        "tokens_saved_by_dedup": session["tokens_saved_by_dedup"],
    }


@router.get("/sessions")
def list_sessions():
    """List all active sessions with their stats."""
    _cleanup_sessions()
    with _sessions_lock:
        return [
            {
                "session_id": s["session_id"],
                "agent_id": s.get("agent_id", ""),
                "profile": s.get("profile", ""),
                "shared": s.get("shared", False),
                "tools_seen": len(s["seen_tool_ids"]),
                "tokens_saved_by_dedup": s["tokens_saved_by_dedup"],
                "expires_at": s["expires_at"].isoformat(),
            }
            for s in _sessions.values()
        ]


# -----------------------------------------------------------------------
# Tool Profiles
# -----------------------------------------------------------------------

@router.post("/profiles", response_model=ProfileInfo)
def create_profile(req: CreateProfileRequest):
    """
    Create a tool profile — a named, reusable subset of tools for an agent type.

    Agents that search with a profile only search within the matched tools —
    not all 5,000+. This gives three wins simultaneously:
    1. Faster search (smaller matrix)
    2. Better accuracy (no noise from irrelevant tools)
    3. Lower tokens (fewer tools to return)

    Example profiles:
        "email-agent"   → tool_patterns: ["GMAIL_*", "OUTLOOK_*"]
        "code-agent"    → tool_patterns: ["GITHUB_*", "GITLAB_*", "LINEAR_*"]
        "data-agent"    → tool_patterns: ["AIRTABLE_*", "NOTION_*", "GOOGLEDRIVE_*"]
        "social-agent"  → tool_patterns: ["TWITTER_*", "LINKEDIN_*", "DISCORDBOT_*"]

    Example:
        POST /v1/profiles
        {
            "name": "email-agent",
            "description": "Agent that handles all email and calendar tasks",
            "tool_patterns": ["GMAIL_*", "OUTLOOK_*", "GOOGLECALENDAR_*"],
            "pinned_tool_ids": ["composio__SLACK_SEND_MESSAGE"]
        }
    """
    with _profiles_lock:
        if req.name in _profiles:
            raise HTTPException(status_code=409, detail=f"Profile already exists: {req.name}")

    # Count how many tools this profile currently matches
    allowed_ids = _resolve_profile_tool_ids(req.name) or set()
    # Need to temporarily store the profile to resolve it
    profile_data = {
        "name": req.name,
        "description": req.description,
        "tool_patterns": req.tool_patterns,
        "pinned_tool_ids": req.pinned_tool_ids,
        "created_at": datetime.utcnow().isoformat(),
    }
    with _profiles_lock:
        _profiles[req.name] = profile_data

    # Now resolve with the profile stored
    allowed_ids = _resolve_profile_tool_ids(req.name) or set()
    _save_profiles()

    return ProfileInfo(
        name=req.name,
        description=req.description,
        tool_patterns=req.tool_patterns,
        pinned_tool_ids=req.pinned_tool_ids,
        tool_count=len(allowed_ids),
        created_at=datetime.utcnow(),
    )


@router.get("/profiles", response_model=list[ProfileInfo])
def list_profiles():
    """List all tool profiles with their current matched tool counts."""
    with _profiles_lock:
        profile_list = list(_profiles.values())

    result = []
    for p in profile_list:
        allowed = _resolve_profile_tool_ids(p["name"]) or set()
        result.append(ProfileInfo(
            name=p["name"],
            description=p.get("description", ""),
            tool_patterns=p.get("tool_patterns", []),
            pinned_tool_ids=p.get("pinned_tool_ids", []),
            tool_count=len(allowed),
            created_at=datetime.fromisoformat(p["created_at"]) if isinstance(p.get("created_at"), str) else datetime.utcnow(),
        ))
    return result


@router.get("/profiles/{profile_name}", response_model=ProfileInfo)
def get_profile(profile_name: str):
    """Get a specific profile with its current matched tool count."""
    with _profiles_lock:
        p = _profiles.get(profile_name)
    if not p:
        raise HTTPException(status_code=404, detail=f"Profile not found: {profile_name}")

    allowed = _resolve_profile_tool_ids(profile_name) or set()
    return ProfileInfo(
        name=p["name"],
        description=p.get("description", ""),
        tool_patterns=p.get("tool_patterns", []),
        pinned_tool_ids=p.get("pinned_tool_ids", []),
        tool_count=len(allowed),
        created_at=datetime.fromisoformat(p["created_at"]) if isinstance(p.get("created_at"), str) else datetime.utcnow(),
    )


@router.delete("/profiles/{profile_name}")
def delete_profile(profile_name: str):
    """Delete a tool profile."""
    with _profiles_lock:
        if profile_name not in _profiles:
            raise HTTPException(status_code=404, detail=f"Profile not found: {profile_name}")
        del _profiles[profile_name]
    _save_profiles()
    return {"status": "deleted", "profile": profile_name}


# -----------------------------------------------------------------------
# Cost Report
# -----------------------------------------------------------------------

@router.get("/cost-report")
def cost_report():
    """
    Token savings & cost report across all agents and sessions.

    Shows the real ROI of running ToolsDNS — how many tokens have been
    saved across all search queries, schema dedup, and profiles.

    Returns:
        - Lifetime token savings from search (not loading full index)
        - Lifetime token savings from session schema dedup
        - Cost saved in USD (per model if TOOLDNS_MODEL is set)
        - Active sessions with their individual savings
        - Cache performance (hit rate)
        - Top searched queries
    """
    from tooldns.tokens import get_model_price, tokens_to_cost

    stats = _database.get_search_stats()
    cache_stats = _search_engine._cache.stats

    # Session dedup savings across all active sessions
    _cleanup_sessions()
    with _sessions_lock:
        active_sessions = list(_sessions.values())

    session_dedup_tokens = sum(s["tokens_saved_by_dedup"] for s in active_sessions)
    total_tokens_saved = stats.get("total_tokens_saved", 0) + session_dedup_tokens

    # Cost calculation
    model_name = _search_engine._get_model()
    price = get_model_price(model_name) if model_name else None
    cost_saved_usd = tokens_to_cost(total_tokens_saved, price) if price else None

    # Active profiles
    with _profiles_lock:
        profile_names = list(_profiles.keys())

    return {
        "lifetime": {
            "total_searches": stats.get("total_searches", 0),
            "tokens_saved_by_search": stats.get("total_tokens_saved", 0),
            "tokens_saved_by_dedup": session_dedup_tokens,
            "total_tokens_saved": total_tokens_saved,
            "cost_saved_usd": round(cost_saved_usd, 4) if cost_saved_usd else None,
            "model": model_name or "not set (set TOOLDNS_MODEL for cost calc)",
        },
        "cache": cache_stats,
        "active_sessions": [
            {
                "session_id": s["session_id"],
                "agent_id": s.get("agent_id", ""),
                "tools_seen": len(s["seen_tool_ids"]),
                "tokens_saved_by_dedup": s["tokens_saved_by_dedup"],
                "expires_at": s["expires_at"].isoformat(),
            }
            for s in active_sessions
        ],
        "active_profiles": profile_names,
        "tools_indexed": _database.get_tool_count(),
    }


# -----------------------------------------------------------------------
# Sources
# -----------------------------------------------------------------------

@router.post("/sources", response_model=SourceResponse)
def add_source(req: SourceRequest):
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
        _search_engine.invalidate_cache()
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
def list_sources(key_info: dict = Depends(require_api_key)):
    """
    List all registered sources with their status and tool counts.

    Admin keys see full config. Sub-keys see name/type/status only.
    """
    sources = _database.get_all_sources()
    is_admin = key_info.get("is_admin", False)
    return [_sanitize_source(s, is_admin) for s in sources]


@router.delete("/sources/{source_id}")
def delete_source(source_id: str):
    """
    Remove a source and all its indexed tools.

    Also writes the source name to disabled_sources.json so auto-discover
    and ingest_local won't re-add it on the next refresh cycle.
    """
    import json as _json
    from tooldns.config import TOOLDNS_HOME

    # Look up the source before deleting so we can record its name
    sources = _database.get_all_sources()
    source = next((s for s in sources if s["id"] == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found.")

    _database.delete_source(source_id)
    _search_engine.invalidate_cache()

    # Persist to disabled_sources.json so it isn't re-ingested on refresh
    disabled_file = TOOLDNS_HOME / "disabled_sources.json"
    try:
        disabled: list = _json.loads(disabled_file.read_text()) if disabled_file.exists() else []
        name = source.get("name", source_id)
        if name not in disabled:
            disabled.append(name)
        disabled_file.write_text(_json.dumps(disabled, indent=2))
    except Exception:
        pass  # Non-fatal — source is deleted from DB regardless

    return {"status": "deleted", "source_id": source_id, "source_name": source.get("name")}


@router.get("/stats")
def get_stats():
    """Search history and token savings statistics."""
    stats = _database.get_search_stats()
    stats["query_cache"] = _search_engine._cache.stats
    return stats


# -----------------------------------------------------------------------
# Tools
# -----------------------------------------------------------------------

@router.get("/categories")
def list_categories():
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
def list_tools(source: str = None, category: str = None):
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
def get_tool(tool_id: str):
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
def call_tool(req: dict):
    """
    Proxy a tool call to the original MCP server.

    This is the execution bridge — the LLM sends the tool name
    and arguments here, and ToolsDNS forwards the call to the
    correct MCP server.

    Request body:
        {
            "tool_id": "tooldns__GMAIL_SEND_EMAIL",
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
        if config.get("path"):
            config_path = Path(os.path.expanduser(config["path"]))
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
        if config.get("path"):
            config_path = Path(os.path.expanduser(config["path"]))
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
def register_mcp(req: RegisterMCPRequest):
    """
    Register a new MCP server into ToolsDNS — callable by AI agents.

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
def list_skills():
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

    Writes a SKILL.md file to the ToolsDNS skills directory (or a
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
        allowed_bases = [str(TOOLDNS_HOME)]
        expanded = os.path.realpath(os.path.expanduser(req.skill_path))
        if not any(expanded.startswith(b) for b in allowed_bases):
            raise HTTPException(status_code=400, detail="skill_path must be within ~/.tooldns")
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
        _search_engine.invalidate_cache()
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

    ToolsDNS detects the type, generates the source config, and optionally
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


@router.get("/connect-info")
async def connect_info(auth: dict = Depends(require_api_key)):
    """
    Return MCP connection config snippets for the authenticated API key.

    The frontend uses this to show users how to connect their MCP clients
    (Claude Desktop, Cursor, Cline, etc.) to this ToolsDNS instance.
    """
    api_key = auth.get("key", settings.api_key)

    # Resolve public base URL: prefer TOOLDNS_PUBLIC_URL / settings.public_url, fall back to port
    public_url = (os.environ.get("TOOLDNS_PUBLIC_URL", "") or settings.public_url).rstrip("/")
    if not public_url:
        public_url = f"http://localhost:{settings.port}"

    mcp_url = f"{public_url}/mcp"

    return {
        "mcp_url": mcp_url,
        "api_key": api_key,
        "snippets": {
            "claude_desktop": {
                "label": "Claude Desktop",
                "file": "~/Library/Application Support/Claude/claude_desktop_config.json",
                "config": {
                    "mcpServers": {
                        "tooldns": {
                            "type": "streamable-http",
                            "url": mcp_url,
                            "headers": {"Authorization": f"Bearer {api_key}"}
                        }
                    }
                }
            },
            "cursor": {
                "label": "Cursor / Windsurf / Zed",
                "note": "Add to your MCP settings (Settings → MCP)",
                "config": {
                    "mcpServers": {
                        "tooldns": {
                            "url": mcp_url,
                            "headers": {"Authorization": f"Bearer {api_key}"}
                        }
                    }
                }
            },
            "stdio": {
                "label": "Any MCP client (stdio fallback)",
                "note": "Use this if your client does not support HTTP MCP. Requires: pip install tooldns",
                "config": {
                    "mcpServers": {
                        "tooldns": {
                            "command": "python3",
                            "args": ["-m", "tooldns.mcp_server"],
                            "env": {
                                "TOOLDNS_API_URL": public_url,
                                "TOOLDNS_API_KEY": api_key
                            }
                        }
                    }
                }
            }
        }
    }


@router.get("/system-prompt")
async def get_system_prompt(format: str = "text"):
    """
    Generate a ready-to-paste system prompt for your AI agent.
    Reflects the live tool count at call time.

    Query params:
        format: "text" (default) or "json"
    """
    total = _database.get_tool_count() if _database else 0
    public_url = (os.environ.get("TOOLDNS_PUBLIC_URL", "") or "").rstrip("/") or "http://localhost:8787"
    app_name = os.environ.get("TOOLDNS_APP_NAME", "ToolsDNS")

    prompt = f"""## {app_name} — Tool Discovery Layer

You have access to **{total:,} tools** indexed in {app_name}. Do NOT try to remember or guess tool names — always use {app_name} to find and run them.

### How to Use {app_name}

**Find the right tool**
```
search_tools(query="send an email")
search_tools(query="create a GitHub issue")
```
Returns the best matching tools with schemas and call instructions.

**Execute a tool**
```
call_tool(tool_id="GMAIL_SEND_EMAIL", arguments={{"to": "alice@example.com", "body": "Hi!"}})
```
Always pass `arguments` as a JSON object, never as a string.

**Discover custom skills**
```
list_skills()        # see all available skills
read_skill("name")   # get full instructions before running a skill
```

### Rules
1. **Never guess tool IDs** — always `search_tools` first.
2. **Never say you can't do something** without searching first — you have {total:,} tools.
3. **For skills**: `list_skills` → `read_skill(name)` → follow the instructions.
4. **File links**: if a tool returns a `download_url`, send that URL to the user — do not embed base64.

*Powered by {app_name} · {public_url}*
"""

    if format == "json":
        return {"system_prompt": prompt, "tools_indexed": total, "public_url": public_url}
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(prompt)
