"""
api.py — FastAPI routes for the ToolsDNS API.

Endpoints:
    POST /v1/search           — Search for tools by natural language query
    POST /v1/search/batch     — Batch search: multiple queries, one HTTP call
    POST /v1/call             — Execute a tool or macro (with analytics tracking)
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

    # Workflows & Macros
    POST /v1/workflows              — Create a workflow pattern
    POST /v1/suggest-workflow       — Suggest workflows by query
    POST /v1/execute-workflow       — Execute a workflow (real tool calls)
    POST /v1/macros                 — Create a macro (reusable multi-tool)
    GET  /v1/macros                 — List all macros
    DELETE /v1/macros/{id}          — Delete a macro

    # Analytics
    GET /v1/analytics/popular       — Most-called tools
    GET /v1/analytics/unused        — Tools never called (cleanup candidates)
    GET /v1/analytics/agents        — Per-agent tool usage stats
    GET /v1/analytics/conversion    — Search-to-call conversion rates

Each endpoint validates input via Pydantic models (see models.py)
and requires a valid API key (see auth.py).
"""

import os
import uuid
import fnmatch
import json as _json
import logging
import threading
import time
import asyncio

logger = logging.getLogger("tooldns")
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from tooldns.config import settings, TOOLDNS_HOME
from tooldns.auth import require_api_key
from tooldns.workflows import WorkflowEngine
from tooldns.models import (
    SearchRequest, SearchResponse,
    BatchSearchRequest, BatchSearchResponse,
    CreateSessionRequest, SessionInfo,
    CreateProfileRequest, ProfileInfo,
    SuggestWorkflowRequest, SuggestWorkflowResponse,
    ExecuteWorkflowRequest, ExecuteWorkflowResponse,
    CreateWorkflowRequest, WorkflowPattern,
    LearnFromUsageRequest, LearnFromUsageResponse,
    SourceRequest, SourceResponse, SourceType,
    RegisterMCPRequest, CreateSkillRequest,
    CallToolRequest, CreateMacroRequest, MacroStep, MacroInfo,
    PreflightRequest, PreflightResponse, PreflightToolMatch,
    SearchSelectRequest, ToolHintsRequest,
    MemoryIngestRequest,
)
from tooldns.caller import call_tool as caller_call_tool, load_skill_content, resolve_args

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
_workflow_engine = None


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
    global _search_engine, _ingestion_pipeline, _database, _health_monitor, _workflow_engine
    _search_engine = search_engine
    _ingestion_pipeline = ingestion_pipeline
    _database = database
    _health_monitor = health_monitor
    _workflow_engine = WorkflowEngine(
        database,
        tool_caller=lambda tid, args: caller_call_tool(database, tid, args)
    )
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
            if not req.agent_id and session.get("agent_id"):
                req.agent_id = session["agent_id"]
        else:
            raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")

    # Get agent preference boosts if agent_id provided
    preference_boosts = {}
    if req.agent_id and _workflow_engine:
        try:
            preference_boosts = _workflow_engine.get_agent_boosts(req.agent_id)
        except Exception as e:
            logger.warning(f"Failed to get agent preferences: {e}")
    
    response = _search_engine.search(
        query=req.query,
        top_k=req.top_k,
        threshold=req.threshold,
        api_key=auth.get("key", ""),
        minimal=req.minimal,
        allowed_tool_ids=allowed_tool_ids,
        seen_tool_ids=seen_tool_ids,
        preference_boosts=preference_boosts if preference_boosts else None,
    )
    response.agent_preferences_applied = len(preference_boosts) > 0

    # Filter by ID prefix (e.g. "memory__" for memory-only search)
    if req.id_prefix:
        response.results = [r for r in response.results if r.id.startswith(req.id_prefix)]

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
# Preflight — server-side intent extraction + multi-strategy search
# -----------------------------------------------------------------------

import re as _re
import concurrent.futures

# Intent patterns: (regex, [search queries])
# Each pattern maps natural language to tool-name-style AND descriptive queries.
_PREFLIGHT_INTENT_MAP: list[tuple[_re.Pattern, list[str]]] = [
    # Email
    (_re.compile(r"\b(send|write|draft|compose|shoot|fire off)\b.*\b(email|mail|message|note)\b", _re.I),
     ["GMAIL_SEND_EMAIL", "gmail send email", "send an email message"]),
    (_re.compile(r"\b(check|read|fetch|get|see|look at)\b.*\b(email|mail|inbox)\b", _re.I),
     ["GMAIL_FETCH_EMAILS", "gmail get inbox emails"]),
    (_re.compile(r"\b(reply|respond)\b.*\b(email|mail|thread)\b", _re.I),
     ["GMAIL_REPLY_TO_THREAD", "gmail reply email"]),
    # Slack
    (_re.compile(r"\b(send|post|write|notify|tell|message)\b.*\b(slack|channel|team)\b", _re.I),
     ["SLACK_SEND_MESSAGE", "slack send message channel"]),
    (_re.compile(r"\b(slack)\b", _re.I),
     ["SLACK_SEND_MESSAGE", "slack channel message"]),
    # Calendar
    (_re.compile(r"\b(schedule|create|book|set up|add)\b.*\b(meeting|event|appointment|call|calendar)\b", _re.I),
     ["GOOGLECALENDAR_CREATE_EVENT", "google calendar create event"]),
    (_re.compile(r"\b(check|show|list|what's on|see)\b.*\b(calendar|schedule|agenda|meetings)\b", _re.I),
     ["GOOGLECALENDAR_FIND_EVENT", "google calendar list events"]),
    # GitHub
    (_re.compile(r"\b(create|open|file|submit)\b.*\b(issue|bug|ticket)\b", _re.I),
     ["GITHUB_CREATE_AN_ISSUE", "github create issue"]),
    (_re.compile(r"\b(create|open|submit)\b.*\b(pr|pull request)\b", _re.I),
     ["GITHUB_CREATE_A_PULL_REQUEST", "github create pull request"]),
    (_re.compile(r"\b(merge|review)\b.*\b(pr|pull request)\b", _re.I),
     ["GITHUB_MERGE_A_PULL_REQUEST", "github merge pull request"]),
    (_re.compile(r"\b(github|repo)\b", _re.I),
     ["GITHUB_LIST_REPOS", "github repository"]),
    # Browser
    (_re.compile(r"\b(open|go to|navigate|visit|browse|check)\b.*\b(website|page|site|url|link)\b", _re.I),
     ["browser_navigate", "playwright navigate open webpage"]),
    (_re.compile(r"\b(click|press|tap)\b.*\b(button|link|element)\b", _re.I),
     ["browser_click", "playwright click button element"]),
    (_re.compile(r"\b(fill|type|enter|input)\b.*\b(form|field|text|box)\b", _re.I),
     ["browser_fill", "playwright fill form input"]),
    (_re.compile(r"\b(screenshot|capture|snap)\b", _re.I),
     ["browser_screenshot", "playwright screenshot capture"]),
    (_re.compile(r"\b(browse|search the web|look up|google)\b", _re.I),
     ["browser_navigate", "web browser search"]),
    # Salesforce
    (_re.compile(r"\b(salesforce|sfdc|sf)\b.*\b(task|create|check)\b", _re.I),
     ["SALESFORCE_CREATE_TASK", "salesforce create task"]),
    (_re.compile(r"\b(salesforce|sfdc|sf)\b", _re.I),
     ["SALESFORCE", "salesforce CRM"]),
    # Google Docs/Sheets/Drive
    (_re.compile(r"\b(create|generate|make)\b.*\b(spreadsheet|excel|csv|sheet)\b", _re.I),
     ["GOOGLESHEETS_CREATE_GOOGLE_SHEET", "google sheets create spreadsheet"]),
    (_re.compile(r"\b(create|write|generate|make)\b.*\b(doc|document|report)\b", _re.I),
     ["GOOGLEDOCS_CREATE_DOCUMENT", "google docs create document"]),
    (_re.compile(r"\b(upload|download|share)\b.*\b(file|document|pdf)\b", _re.I),
     ["GOOGLEDRIVE_UPLOAD_FILE", "google drive file upload"]),
    # Tasks
    (_re.compile(r"\b(create|add|make)\b.*\b(task|todo|reminder)\b", _re.I),
     ["create task todo", "TODOIST_CREATE_TASK"]),
    (_re.compile(r"\b(list|show|check)\b.*\b(tasks?|todos?)\b", _re.I),
     ["list tasks", "TODOIST_GET_TASKS"]),
    # Twitter
    (_re.compile(r"\b(tweet|post|publish)\b.*\b(twitter|x\.com|social)\b", _re.I),
     ["TWITTER_CREATION_OF_A_TWEET", "twitter post tweet"]),
    # Notion
    (_re.compile(r"\b(notion)\b", _re.I),
     ["NOTION_CREATE_A_PAGE", "notion page workspace"]),
    # Linear
    (_re.compile(r"\b(linear)\b.*\b(issue|ticket|bug)\b", _re.I),
     ["LINEAR_CREATE_LINEAR_ISSUE", "linear create issue"]),
    (_re.compile(r"\b(linear)\b", _re.I),
     ["LINEAR", "linear project issue"]),
    # Jira
    (_re.compile(r"\b(jira)\b", _re.I),
     ["JIRA_CREATE_ISSUE", "jira project issue"]),
    # Discord
    (_re.compile(r"\b(discord)\b.*\b(send|message|post)\b", _re.I),
     ["DISCORD_SEND_MESSAGE", "discord send message"]),
    (_re.compile(r"\b(discord)\b", _re.I),
     ["DISCORD", "discord server channel"]),
    # Telegram
    (_re.compile(r"\b(telegram)\b", _re.I),
     ["TELEGRAM_SEND_MESSAGE", "telegram bot message"]),
    # WhatsApp
    (_re.compile(r"\b(whatsapp|whats app)\b", _re.I),
     ["WHATSAPP_SEND_MESSAGE", "whatsapp send message"]),
    # Reddit
    (_re.compile(r"\b(reddit)\b.*\b(news|posts?|search|check|latest|top|hot)\b", _re.I),
     ["REDDIT_SEARCH_ACROSS_SUBREDDITS", "reddit search posts news"]),
    (_re.compile(r"\b(reddit)\b", _re.I),
     ["REDDIT_SEARCH_ACROSS_SUBREDDITS", "reddit"]),
    # HackerNews
    (_re.compile(r"\b(hacker\s*news|hn|ycombinator)\b", _re.I),
     ["HACKERNEWS_SEARCH_POSTS", "hacker news search"]),
    # News / headlines
    (_re.compile(r"\b(news|headlines|latest)\b.*\b(ai|tech|artificial|machine learning)\b", _re.I),
     ["REDDIT_SEARCH_ACROSS_SUBREDDITS", "HACKERNEWS_SEARCH_POSTS", "AI news headlines"]),
    # Generic notify
    (_re.compile(r"\b(notify|alert|inform|tell)\b.*\b(someone|team|user|them)\b", _re.I),
     ["send notification", "SLACK_SEND_MESSAGE", "GMAIL_SEND_EMAIL"]),
]

# Regex to clean user data from queries
_QUERY_CLEAN_RE = _re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+"     # emails
    r"|https?://\S+"                 # URLs
    r"|\+?\d[\d\s\-()]{7,}\d"       # phone numbers
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"  # dates
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}",  # more emails
    _re.I,
)


def _preflight_clean_query(text: str) -> str:
    """Strip user data (emails, URLs, numbers) for cleaner tool search."""
    cleaned = _QUERY_CLEAN_RE.sub("", text)
    cleaned = _re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= 8 else text


def _preflight_extract_intents(text: str) -> list[str]:
    """Extract tool-friendly search queries using intent patterns.

    Supports compound messages: splits on 'and', 'then', 'also', '+', ','
    and extracts intents from each sub-clause independently.
    """
    # Split compound messages into sub-clauses
    splitter = _re.compile(r"\b(?:and\s+(?:also\s+)?|then\s+|also\s+|plus\s+|\+|,\s*(?:and\s+)?)\b", _re.I)
    clauses = splitter.split(text)
    # Always include the full text as a clause too
    if len(clauses) > 1:
        clauses.append(text)

    queries: list[str] = []
    seen: set[str] = set()
    for clause in clauses:
        clause = clause.strip()
        if len(clause) < 5:
            continue
        matched_this_clause = 0
        for pattern, query_list in _PREFLIGHT_INTENT_MAP:
            if pattern.search(clause):
                for q in query_list:
                    ql = q.lower()
                    if ql not in seen:
                        seen.add(ql)
                        queries.append(q)
                matched_this_clause += 1
                # 1 match per clause is enough — move to next clause
                if matched_this_clause >= 1:
                    break
        if len(queries) >= 8:
            break
    return queries


def _compact_schema_text(schema: dict) -> str:
    """Build compact parameter summary."""
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not props:
        return "    (no parameters)"
    lines = []
    for pname, pinfo in list(props.items())[:10]:
        ptype = pinfo.get("type", "any")
        req = " [REQUIRED]" if pname in required else ""
        desc = pinfo.get("description", "").split(".")[0].split("\n")[0][:60]
        lines.append(f"    {pname}: {ptype}{req} — {desc}")
    if len(props) > 10:
        lines.append(f"    ...and {len(props) - 10} more")
    return "\n".join(lines)


@router.post("/preflight", response_model=PreflightResponse)
def preflight_search(req: PreflightRequest, auth: dict = Depends(require_api_key)):
    """
    Server-side preflight tool discovery.

    Send a raw user message and get back an LLM-ready context block with
    the most relevant tools, their schemas, and call templates. Designed
    to be called BEFORE the LLM loop in any agent framework.

    How it works:
    1. Cleans the user message (strips emails, URLs, dates)
    2. Extracts intent keywords using built-in patterns
    3. Runs multiple parallel searches (cleaned query + intent queries)
    4. Merges, deduplicates, ranks by best confidence
    5. Returns results as an injectable context block or structured JSON

    Usage (any framework):
        # Before your LLM call:
        resp = requests.post("/v1/preflight", json={"message": user_msg})
        if resp.json()["found"]:
            enriched_msg = user_msg + "\\n\\n" + resp.json()["context_block"]
            # Pass enriched_msg to your LLM
    """
    import time as _time
    start = _time.perf_counter()

    text = req.message.strip()
    if len(text) < 10:
        return PreflightResponse(found=False)

    # Hybrid approach:
    # 1. Split compound messages into sub-clauses (dynamic)
    # 2. Also extract intent keywords from patterns (for name-style matching)
    # 3. Search each separately, merge results

    splitter = _re.compile(r"\b(?:and\s+(?:also\s+)?|then\s+|also\s+|plus\s+|after\s+that\s+|\+|,\s*(?:and\s+)?)\b", _re.I)
    clauses = splitter.split(text)

    search_queries: list[str] = []
    seen_q: set[str] = set()

    # Add each cleaned sub-clause as a search query
    for clause in clauses:
        cleaned_clause = _preflight_clean_query(clause.strip())
        if len(cleaned_clause) >= 8 and cleaned_clause.lower() not in seen_q:
            seen_q.add(cleaned_clause.lower())
            search_queries.append(cleaned_clause)

    # Also add intent-extracted queries (name-style like GMAIL_SEND_EMAIL)
    # These boost matching against tool names, not just descriptions
    intent_queries = _preflight_extract_intents(text)
    for iq in intent_queries:
        if iq.lower() not in seen_q:
            seen_q.add(iq.lower())
            search_queries.append(iq)

    # Fallback: if nothing was extracted, use the full cleaned message
    if not search_queries:
        search_queries = [_preflight_clean_query(text)]

    # Get preference boosts
    preference_boosts = {}
    if req.agent_id and _workflow_engine:
        try:
            preference_boosts = _workflow_engine.get_agent_boosts(req.agent_id)
        except Exception:
            pass

    # Run all searches and merge results
    all_results: dict[str, dict] = {}  # tool_id -> best result + matched_by

    for q in search_queries[:8]:
        try:
            response = _search_engine.search(
                query=q,
                top_k=req.top_k,
                threshold=req.threshold,
                api_key=auth.get("key", ""),
                minimal=False,
                preference_boosts=preference_boosts if preference_boosts else None,
            )
            for r in response.results:
                existing = all_results.get(r.id)
                if not existing or r.confidence > existing["confidence"]:
                    all_results[r.id] = {
                        "tool_id": r.id,
                        "name": r.name,
                        "description": r.description,
                        "confidence": r.confidence,
                        "input_schema": r.input_schema,
                        "source_type": r.source,
                        "matched_by": q,
                    }
        except Exception:
            continue

    if not all_results:
        elapsed = (_time.perf_counter() - start) * 1000
        return PreflightResponse(found=False, queries_used=search_queries[:8], search_time_ms=elapsed)

    # Sort and limit
    sorted_results = sorted(all_results.values(), key=lambda r: r["confidence"], reverse=True)[:req.max_results]

    # Build tool matches
    tools = []
    for i, r in enumerate(sorted_results):
        schema = r["input_schema"] if req.include_schemas and i < 3 else {}
        call_template = None
        if req.include_call_templates and i < 2 and schema.get("properties"):
            props = schema.get("properties", {})
            required = set(schema.get("required", []))
            tmpl_args = {}
            for pname in props:
                if pname in required:
                    tmpl_args[pname] = f"<{pname}>"
            if tmpl_args:
                call_template = f'toolsdns(action="call", tool_id="{r["tool_id"]}", arguments={_json.dumps(tmpl_args)})'
        tools.append(PreflightToolMatch(
            tool_id=r["tool_id"],
            name=r["name"],
            description=r["description"][:200],
            confidence=r["confidence"],
            input_schema=schema,
            call_template=call_template,
            source_type=r["source_type"],
            matched_by=r["matched_by"],
        ))

    # Check for macros
    macros_list: list[str] = []
    if req.include_macros:
        try:
            macros_file = TOOLDNS_HOME / "macros.json"
            if macros_file.exists():
                macros_data = _json.loads(macros_file.read_text())
                if isinstance(macros_data, list):
                    macros_list = [f"macro__{m.get('name', '?')}" for m in macros_data]
                elif isinstance(macros_data, dict):
                    macros_list = [f"macro__{m.get('name', '?')}" for m in macros_data.get("macros", [])]
        except Exception:
            pass

    # Build context block — action instruction FIRST, then tool details
    context_block = None
    if req.format == "context_block":
        # Find the best non-skill tool with a call template for the quick-call line
        best = tools[0] if tools else None
        best_call = None
        for t in tools[:3]:
            if t.call_template:
                best_call = t
                break

        lines = [
            "[ToolsDNS Auto-Discovery] — tools already found, DO NOT search again.",
            "",
        ]

        # Put the direct call instruction FIRST
        if best_call:
            lines.append(f">>> CALL THIS NOW: {best_call.call_template}")
            lines.append(f"    (tool: {best_call.name} — {best_call.description[:80]})")
            lines.append("")
        elif best:
            lines.append(f'>>> CALL THIS NOW: toolsdns(action="call", tool_id="{best.tool_id}", arguments={{}})')
            lines.append(f"    (tool: {best.name} — {best.description[:80]})")
            lines.append("")

        lines.append("RULES: Your FIRST tool call must be toolsdns action=call. Do NOT action=search. Do NOT action=get. Do NOT use mcp_tooldns_* tools.")
        lines.append("")

        # Then show all matches with schemas
        for i, t in enumerate(tools):
            lines.append(f"{'BEST MATCH' if i == 0 else f'Match {i+1}'}  [{t.confidence:.0%}]  TOOL_ID: {t.tool_id}")
            lines.append(f"  {t.name} — {t.description[:120]}")
            if t.input_schema and t.input_schema.get("properties"):
                lines.append(_compact_schema_text(t.input_schema))
            if t.call_template and t != best_call:
                lines.append(f"  CALL: {t.call_template}")
            elif "skill" in str(t.source_type).lower():
                lines.append(f'  SKILL: use toolsdns(action="get", tool_id="{t.tool_id}") for instructions')
            lines.append("")

        if macros_list:
            lines.append(f"MACROS available: {', '.join(macros_list)}")
            lines.append("")

        context_block = "\n".join(lines)

    elapsed = (_time.perf_counter() - start) * 1000
    return PreflightResponse(
        found=True,
        tools=tools,
        macros=macros_list,
        context_block=context_block,
        queries_used=search_queries[:8],
        search_time_ms=elapsed,
    )


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

    # Get agent preference boosts if agent_id provided
    preference_boosts = {}
    if req.agent_id and _workflow_engine:
        try:
            preference_boosts = _workflow_engine.get_agent_boosts(req.agent_id)
        except Exception as e:
            logger.warning(f"Failed to get agent preferences: {e}")

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
            preference_boosts=preference_boosts if preference_boosts else None,
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
# Search Select — record tool selection without calling the tool
# -----------------------------------------------------------------------

@router.post("/search/select")
def search_select(req: SearchSelectRequest):
    """
    Record which search result an agent selected.

    Allows agents to report tool selections without executing the tool
    via /v1/call. Updates agent preferences for personalized search.
    """
    if not _workflow_engine:
        raise HTTPException(status_code=503, detail="Workflow engine not initialized")
    _workflow_engine.record_tool_selection(req.agent_id, req.tool_id, req.query, req.confidence)
    return {"status": "ok", "agent_id": req.agent_id, "tool_id": req.tool_id}


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
def list_profiles(response: Response = None):
    """List all tool profiles with their current matched tool counts."""
    if response:
        response.headers["Cache-Control"] = "public, max-age=60"

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
def list_sources(key_info: dict = Depends(require_api_key), response: Response = None):
    """
    List all registered sources with their status and tool counts.

    Admin keys see full config. Sub-keys see name/type/status only.
    """
    if response:
        response.headers["Cache-Control"] = "public, max-age=60"

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
def list_tools(source: str = None, category: str = None, response: Response = None):
    """
    List all indexed tools, optionally filtered by source or category.

    Args:
        source: Optional source name to filter by.
        category: Optional category name to filter by (e.g. "Dev & Code").

    Returns:
        dict: Tool list with count.
    """
    if response:
        response.headers["Cache-Control"] = "public, max-age=300"

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
def get_tool(tool_id: str, response: Response = None):
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
    import hashlib

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
        skill_content = load_skill_content(tool["name"], source_info)
        if skill_content:
            result["skill_content"] = skill_content

    # Cache headers + ETag
    if response:
        response.headers["Cache-Control"] = "public, max-age=300"
        etag = hashlib.md5(json_mod.dumps(result, sort_keys=True).encode()).hexdigest()
        response.headers["ETag"] = f'"{etag}"'

    return result


# -----------------------------------------------------------------------
# Tool Execution
# -----------------------------------------------------------------------

@router.post("/call")
def call_tool_endpoint(req: CallToolRequest):
    """
    Execute a tool by ID, or run a macro (prefix: macro__).

    This is the execution bridge — the LLM sends the tool name
    and arguments here, and ToolsDNS forwards the call to the
    correct MCP server. Also records the call for analytics and
    agent preference learning.

    Examples:
        # Single tool call
        POST /v1/call
        {"tool_id": "composio__GMAIL_SEND_EMAIL", "arguments": {"to": "john@example.com"}}

        # Macro call (executes all steps)
        POST /v1/call
        {"tool_id": "macro__deploy-and-notify", "arguments": {"version": "1.2.0"}}
    """
    tool_id = req.tool_id
    arguments = req.arguments

    # --- Macro execution ---
    if tool_id.startswith("macro__"):
        macro = _database.get_workflow(tool_id) if _database else None
        if not macro or macro.get("source") != "macro":
            raise HTTPException(status_code=404, detail=f"Macro not found: {tool_id}")

        results = []
        for step in macro.get("steps", []):
            step_tool_id = step.get("tool_id", "")
            resolved = resolve_args(step.get("arg_mapping", {}), arguments)
            try:
                result = caller_call_tool(_database, step_tool_id, resolved)
                results.append({"tool_id": step_tool_id, "status": "completed", "result": result})
            except Exception as e:
                results.append({"tool_id": step_tool_id, "status": "failed", "error": str(e)})
                if step.get("on_error", "stop") == "stop":
                    break

        _database.increment_workflow_usage(tool_id)
        return {"type": "macro_result", "macro_id": tool_id, "steps": results}

    # --- Single tool call ---
    # Server-side param sanitization: strip args not in tool's schema
    if _database and arguments:
        tool_meta = _database.get_tool_by_id(tool_id)
        if tool_meta:
            schema = tool_meta.get("input_schema", tool_meta.get("inputSchema", {}))
            valid_params = set(schema.get("properties", {}).keys())
            if valid_params:
                clean = {k: v for k, v in arguments.items() if k in valid_params}
                if clean != arguments:
                    removed = set(arguments) - set(clean)
                    logger.info(f"Sanitized args for {tool_id}: stripped {removed}")
                    arguments = clean
    try:
        result = caller_call_tool(_database, tool_id, arguments)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Record for analytics + agent preference learning
    agent_id = req.agent_id or "anonymous"
    if _workflow_engine:
        try:
            _workflow_engine.record_tool_selection(
                agent_id=agent_id,
                tool_id=tool_id,
                query=req.query,
                confidence=0.0
            )
        except Exception as e:
            logger.warning(f"Failed to record tool selection: {e}")
    elif _database:
        try:
            _database.log_tool_call(agent_id, tool_id, req.query)
        except Exception as e:
            logger.warning(f"Failed to log tool call: {e}")

    # Log successful args for tool memory / hints
    is_error = (isinstance(result, dict) and result.get("isError"))
    if _database and arguments and not is_error:
        try:
            _database.log_successful_args(agent_id, tool_id, arguments)
        except Exception as e:
            logger.warning(f"Failed to log successful args: {e}")

    return result


# -----------------------------------------------------------------------
# Tool Hints (Tool Memory)
# -----------------------------------------------------------------------

@router.post("/tool-hints")
def get_tool_hints(req: ToolHintsRequest):
    """Batch-get successful argument patterns for multiple tools."""
    if not _database or not req.tool_ids:
        return {"hints": {}, "found": False}
    hints = _database.get_tool_hints(req.agent_id, req.tool_ids)
    return {"hints": hints, "found": bool(hints)}


# -----------------------------------------------------------------------
# Memory Ingest (Hybrid Memory System)
# -----------------------------------------------------------------------

@router.post("/memory/ingest")
def memory_ingest(req: MemoryIngestRequest):
    """Index memory chunks (knowledge, learnings, rules, history) for semantic search."""
    if not _database or not req.chunks:
        return {"indexed": 0}

    # Delete existing memory entries for files being re-indexed
    file_paths = {c.file_path for c in req.chunks if c.file_path}
    if file_paths:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(_database.db_path)
        for fp in file_paths:
            conn.execute(
                "DELETE FROM tools WHERE json_extract(source_info, '$.source_type') = 'memory' "
                "AND json_extract(source_info, '$.file_path') = ?",
                [fp],
            )
        conn.commit()
        conn.close()

    # Build tool entries from chunks
    tools = []
    for c in req.chunks:
        tools.append({
            "name": c.title,
            "description": c.content[:1000],
            "inputSchema": {},
            "_source_server": "memory",
            "_source_type": "memory",
        })

    # Embed and upsert
    if _search_engine and tools:
        try:
            embedder = _search_engine.embedder
            embedded = []
            descs = [t["description"] for t in tools]
            vectors = embedder.embed_batch(descs)
            for i, t in enumerate(tools):
                chunk = req.chunks[i]
                embedded.append({
                    "tool_id": chunk.chunk_id,
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": {},
                    "source_info": {
                        "source_type": "memory",
                        "source_name": "memory",
                        "original_name": t["name"],
                        "server": "memory",
                        "file_path": chunk.file_path,
                        "section": chunk.section,
                    },
                    "embedding": vectors[i] if i < len(vectors) else None,
                    "tags": ["memory"],
                })
            _database.upsert_tools_batch(embedded)
            logger.info(f"Memory ingest: indexed {len(embedded)} chunks from {len(file_paths)} files")
            return {"indexed": len(embedded)}
        except Exception as e:
            logger.error(f"Memory ingest failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return {"indexed": 0}


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
            "name": "tooldns-skills",
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


# ---------------------------------------------------------------------------
# Workflow / Smart Tool Chaining
# ---------------------------------------------------------------------------

@router.post("/suggest-workflow")
def suggest_workflow(req: SuggestWorkflowRequest):
    """
    Suggest workflows based on a natural language query.
    
    ToolsDNS matches the query against learned workflow trigger phrases
    and returns the best matching workflows with confidence scores.
    
    Example:
        POST /v1/suggest-workflow
        {"query": "onboard a new employee", "agent_id": "hr-bot"}
    
    Returns:
        List of suggested workflows with steps and confidence.
    """
    if not _workflow_engine:
        raise HTTPException(status_code=503, detail="Workflow engine not initialized")
    
    workflows = _workflow_engine.suggest_workflows(
        query=req.query,
        agent_id=req.agent_id,
        top_k=3
    )
    
    return {
        "suggested_workflows": workflows[:1] if workflows else [],
        "alternative_workflows": workflows[1:] if len(workflows) > 1 else [],
        "query": req.query
    }


@router.post("/workflows")
def create_workflow(req: CreateWorkflowRequest):
    """
    Manually create a workflow pattern.
    
    Example:
        POST /v1/workflows
        {
            "name": "Deploy and Announce",
            "trigger_phrases": ["deploy", "ship it"],
            "steps": [
                {"tool_id": "composio__GITHUB_CREATE_RELEASE", "purpose": "Create release"},
                {"tool_id": "composio__SLACK_SEND_MESSAGE", "purpose": "Notify team"}
            ]
        }
    """
    if not _workflow_engine:
        raise HTTPException(status_code=503, detail="Workflow engine not initialized")
    
    import hashlib
    workflow_id = f"wp_manual_{hashlib.md5(req.name.encode()).hexdigest()[:8]}"
    
    # Check if exists
    existing = _database.get_workflow(workflow_id) if _database else None
    if existing:
        raise HTTPException(status_code=409, detail=f"Workflow already exists: {workflow_id}")
    
    workflow = {
        "id": workflow_id,
        "name": req.name,
        "description": req.description,
        "trigger_phrases": req.trigger_phrases,
        "steps": [s.model_dump() for s in req.steps],
        "parallel_groups": req.parallel_groups,
        "usage_count": 0,
        "success_rate": 0.0,
        "avg_completion_time_ms": 0.0,
        "source": "manual",
        "created_by": "api",
        "created_at": datetime.utcnow().isoformat(),
        "last_used_at": datetime.utcnow().isoformat()
    }
    
    _database.upsert_workflow(workflow)
    
    return workflow


@router.get("/workflows")
def list_workflows(source: str = None):
    """List all workflow patterns."""
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    workflows = _database.get_all_workflows(source=source)
    return {"workflows": workflows, "total": len(workflows)}


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str):
    """Get a specific workflow by ID."""
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    workflow = _database.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    
    return workflow


@router.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: str):
    """Delete a workflow pattern."""
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    _database.delete_workflow(workflow_id)
    return {"status": "deleted", "workflow_id": workflow_id}


@router.post("/execute-workflow")
async def execute_workflow(req: ExecuteWorkflowRequest):
    """
    Execute a workflow with the given arguments.
    
    Example:
        POST /v1/execute-workflow
        {
            "workflow_id": "wp_abc123",
            "args": {"employee_name": "Sarah"},
            "execution_mode": "parallel"
        }
    """
    if not _workflow_engine:
        raise HTTPException(status_code=503, detail="Workflow engine not initialized")
    
    try:
        result = await _workflow_engine.execute_workflow(
            workflow_id=req.workflow_id,
            args=req.args,
            execution_mode=req.execution_mode,
            session_id=req.session_id
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}")


@router.post("/learn")
def trigger_learning(req: LearnFromUsageRequest = None):
    """
    Trigger workflow learning from recent tool usage.
    
    Analyzes tool call sequences and creates/updates workflow patterns
    for frequently occurring sequences.
    
    This runs automatically in the background, but can be triggered
    manually for immediate feedback.
    
    Example:
        POST /v1/learn
        {"time_window_hours": 1, "min_occurrences": 3}
    """
    if not _workflow_engine:
        raise HTTPException(status_code=503, detail="Workflow engine not initialized")
    
    if req is None:
        req = LearnFromUsageRequest()
    
    result = _workflow_engine.learn_from_usage(
        time_window_minutes=req.time_window_hours * 60,
        min_occurrences=req.min_occurrences,
    )
    
    return result


# ---------------------------------------------------------------------------
# Agent Preferences (Agent Memory)
# ---------------------------------------------------------------------------

@router.get("/agents/{agent_id}/preferences")
def get_agent_preferences(agent_id: str):
    """
    Get learned preferences for an agent.
    
    Shows which tools the agent prefers and how often they're selected.
    """
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    prefs = _database.get_agent_preferences(agent_id)
    if not prefs:
        return {
            "agent_id": agent_id,
            "preferred_tools": [],
            "tool_selection_counts": {},
            "message": "No preferences learned yet. Agent needs to select tools from search results."
        }
    
    return prefs


@router.get("/agents")
def list_agents():
    """List all agents with learned preferences."""
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")

    prefs = _database.get_all_agent_preferences()
    return {
        "agents": [{"agent_id": p["agent_id"], "tools_preferred": len(p["preferred_tools"])} for p in prefs],
        "total": len(prefs)
    }


# ---------------------------------------------------------------------------
# Tool Performance Analytics
# ---------------------------------------------------------------------------

@router.get("/analytics/popular")
def analytics_popular(limit: int = 20):
    """
    Get most-called tools ranked by call count.

    Returns tools sorted by how often agents actually execute them —
    not just search for them. Use this to identify which tools deliver
    real value and which are noise.

    Args:
        limit: Max results (default 20).
    """
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")

    tools = _database.get_popular_tools(limit=limit)
    return {"popular_tools": tools, "total": len(tools)}


@router.get("/analytics/unused")
def analytics_unused():
    """
    Get tools that have never been called.

    These are candidates for removal — they bloat the search index
    without delivering value. Removing dead tools makes search
    faster and more accurate.
    """
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")

    tools = _database.get_unused_tools()
    return {
        "unused_tools": tools,
        "total": len(tools),
        "suggestion": "Consider removing tools not called in 30+ days to improve search speed."
    }


@router.get("/analytics/agents")
def analytics_agents():
    """
    Get per-agent tool usage statistics.

    Shows which agents are most active, how many unique tools they use,
    and their favorite tools. Feeds into agent preference learning.
    """
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")

    agents = _database.get_agent_tool_stats()
    return {"agents": agents, "total": len(agents)}


@router.get("/analytics/conversion")
def analytics_conversion(limit: int = 20):
    """
    Get search-to-call conversion rates.

    Tools with high search but low call rates may need better
    descriptions or may be duplicates of preferred alternatives.
    """
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")

    tools = _database.get_search_to_call_conversion(limit=limit)
    return {"tools": tools, "total": len(tools)}


# ---------------------------------------------------------------------------
# Macros (Reusable Multi-Tool Workflows)
# ---------------------------------------------------------------------------

@router.post("/macros", response_model=MacroInfo)
def create_macro(req: CreateMacroRequest):
    """
    Create a reusable macro — a multi-tool workflow callable as one tool.

    Once created, call it via POST /v1/call with tool_id="macro__<name>".
    Arguments are resolved using {placeholder} syntax in step arg_templates.

    Example:
        POST /v1/macros
        {
            "name": "deploy-and-notify",
            "description": "Create release, notify Slack, post tweet",
            "steps": [
                {"tool_id": "GITHUB_CREATE_RELEASE", "arg_template": {"tag": "{version}"}},
                {"tool_id": "SLACK_SEND_MESSAGE", "arg_template": {"text": "Deployed {version}"}},
                {"tool_id": "TWITTER_POST", "arg_template": {"text": "v{version} is live!"}}
            ]
        }

        # Then call:
        POST /v1/call
        {"tool_id": "macro__deploy-and-notify", "arguments": {"version": "1.2.0"}}
    """
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")

    import hashlib
    macro_id = f"macro__{req.name}"

    existing = _database.get_workflow(macro_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Macro already exists: {macro_id}")

    steps = []
    for i, s in enumerate(req.steps, 1):
        steps.append({
            "step_number": i,
            "tool_id": s.tool_id,
            "tool_name": s.tool_id.split("__")[-1] if "__" in s.tool_id else s.tool_id,
            "purpose": "",
            "arg_mapping": s.arg_template,
            "arg_defaults": {},
            "depends_on": [],
            "condition": "",
            "on_error": "stop",
            "retry_count": 0
        })

    workflow = {
        "id": macro_id,
        "name": req.name,
        "description": req.description,
        "trigger_phrases": [],
        "steps": steps,
        "parallel_groups": [],
        "usage_count": 0,
        "success_rate": 0.0,
        "avg_completion_time_ms": 0.0,
        "source": "macro",
        "created_by": "api",
        "created_at": datetime.utcnow().isoformat(),
        "last_used_at": datetime.utcnow().isoformat()
    }

    _database.upsert_workflow(workflow)

    return MacroInfo(
        id=macro_id,
        name=req.name,
        description=req.description,
        steps=req.steps,
        usage_count=0,
        created_at=datetime.utcnow(),
    )


@router.get("/macros")
def list_macros():
    """
    List all macros.

    Macros are reusable multi-tool workflows stored as workflow patterns
    with source="macro". Call them via POST /v1/call with the macro ID.
    """
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")

    workflows = _database.get_all_workflows(source="macro")
    macros = []
    for wf in workflows:
        steps = []
        for s in wf.get("steps", []):
            steps.append({
                "tool_id": s.get("tool_id", ""),
                "arg_template": s.get("arg_mapping", {})
            })
        macros.append({
            "id": wf["id"],
            "name": wf["name"],
            "description": wf.get("description", ""),
            "steps": steps,
            "usage_count": wf.get("usage_count", 0),
            "created_at": wf.get("created_at"),
        })
    return {"macros": macros, "total": len(macros)}


@router.delete("/macros/{macro_id}")
def delete_macro(macro_id: str):
    """Delete a macro."""
    if not _database:
        raise HTTPException(status_code=503, detail="Database not initialized")

    # Ensure the ID has the prefix
    full_id = macro_id if macro_id.startswith("macro__") else f"macro__{macro_id}"
    macro = _database.get_workflow(full_id)
    if not macro or macro.get("source") != "macro":
        raise HTTPException(status_code=404, detail=f"Macro not found: {macro_id}")

    _database.delete_workflow(full_id)
    return {"status": "deleted", "macro_id": full_id}
