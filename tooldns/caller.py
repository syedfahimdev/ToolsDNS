"""
caller.py — Tool execution engine for ToolsDNS.

Shared module that handles calling tools via MCP (stdio/HTTP) or returning
skill content. Used by both the /v1/call endpoint and workflow execution.

This module was extracted from api.py to break circular dependencies
and enable workflow execution to call real tools.
"""

import os
import re
import json
import time
import hashlib
import threading
import httpx
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from tooldns.config import logger, TOOLDNS_HOME
from tooldns.fetcher import MCPFetcher


# ---------------------------------------------------------------------------
# MCP Session pool — reuse sessions to avoid handshake on every call
# ---------------------------------------------------------------------------

_session_lock = threading.Lock()
_sessions: dict[str, dict] = {}  # url -> {"session_id": str, "headers": dict, "client": httpx.Client, "created": float}
_SESSION_TTL = 300.0  # 5 minutes

# ---------------------------------------------------------------------------
# Result cache for read-only tool calls
# ---------------------------------------------------------------------------

_result_cache_lock = threading.Lock()
_result_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_RESULT_CACHE_TTL = 600.0  # 10 minutes
_RESULT_CACHE_MAX = 128

# Read-only tool name patterns (prefixes that indicate read/list/get operations)
_READ_ONLY_PREFIXES = (
    "GMAIL_FETCH", "GMAIL_LIST", "GMAIL_GET",
    "GOOGLECALENDAR_FIND", "GOOGLECALENDAR_LIST", "GOOGLECALENDAR_GET",
    "GOOGLE_CALENDAR_FIND", "GOOGLE_CALENDAR_LIST", "GOOGLE_CALENDAR_GET",
    "REDDIT_GET", "REDDIT_LIST", "REDDIT_FETCH",
    "SLACK_LIST", "SLACK_GET",
    "GITHUB_LIST", "GITHUB_GET",
)


def _is_read_only(tool_name: str) -> bool:
    """Check if a tool call is read-only and safe to cache."""
    upper = tool_name.upper()
    return any(upper.startswith(p) or upper.endswith(p) for p in _READ_ONLY_PREFIXES)


def _cache_key(tool_name: str, arguments: dict) -> str:
    """Generate a stable cache key from tool name + arguments."""
    raw = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached_result(tool_name: str, arguments: dict) -> Optional[dict]:
    """Return cached result if available and not expired."""
    if not _is_read_only(tool_name):
        return None
    key = _cache_key(tool_name, arguments)
    with _result_cache_lock:
        entry = _result_cache.get(key)
        if entry is None:
            return None
        expires_at, result = entry
        if time.monotonic() > expires_at:
            del _result_cache[key]
            return None
        _result_cache.move_to_end(key)
        logger.debug("Result cache HIT for {}", tool_name)
        return result


def _set_cached_result(tool_name: str, arguments: dict, result: dict) -> None:
    """Cache a read-only tool result."""
    if not _is_read_only(tool_name):
        return
    key = _cache_key(tool_name, arguments)
    with _result_cache_lock:
        _result_cache[key] = (time.monotonic() + _RESULT_CACHE_TTL, result)
        if len(_result_cache) > _RESULT_CACHE_MAX:
            _result_cache.popitem(last=False)
    logger.debug("Result cache SET for {} (ttl={}s)", tool_name, _RESULT_CACHE_TTL)


# ---------------------------------------------------------------------------
# Argument resolution for workflows/macros
# ---------------------------------------------------------------------------

def resolve_args(template: dict, context: dict, step_results: Optional[dict] = None) -> dict:
    """
    Resolve {placeholder} variables in argument templates.

    Supports:
        - {variable}         — from context dict
        - {step.N.field}     — from previous step results
        - Literal values     — passed through unchanged

    Args:
        template: Argument template dict with {placeholder} strings.
        context: User-provided arguments.
        step_results: Dict mapping step_number -> result dict.

    Returns:
        Resolved arguments dict.
    """
    if not template:
        return {}

    resolved = {}
    for key, value in template.items():
        if isinstance(value, str) and "{" in value:
            resolved[key] = _resolve_string(value, context, step_results)
        elif isinstance(value, dict):
            resolved[key] = resolve_args(value, context, step_results)
        else:
            resolved[key] = value
    return resolved


def _resolve_string(s: str, context: dict, step_results: Optional[dict] = None) -> str:
    """Resolve {placeholder} patterns in a single string."""
    def replacer(match):
        ref = match.group(1)
        # Step result reference: {step.1.content}
        if ref.startswith("step.") and step_results:
            parts = ref.split(".", 2)
            if len(parts) == 3:
                step_num = int(parts[1])
                field = parts[2]
                step_res = step_results.get(step_num, {})
                return str(step_res.get(field, match.group(0)))
        # Context variable
        if ref in context:
            return str(context[ref])
        return match.group(0)

    return re.sub(r'\{([^}]+)\}', replacer, s)


# ---------------------------------------------------------------------------
# Skill content loading
# ---------------------------------------------------------------------------

def load_skill_content(tool_name: str, source_info: dict) -> str:
    """
    Load the full skill file content for a skill-type tool.

    Searches through known skill directories for the matching
    SKILL.md or .md file.
    """
    skill_dirs = []

    config_file = TOOLDNS_HOME / "config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            for sp in config.get("skillPaths", []):
                p = Path(sp).expanduser()
                if p.exists():
                    skill_dirs.append(p)
        except Exception:
            pass

    local_skills = TOOLDNS_HOME / "skills"
    if local_skills.exists():
        skill_dirs.append(local_skills)

    for skill_dir in skill_dirs:
        for item in skill_dir.iterdir():
            if item.is_dir():
                skill_file = item / "SKILL.md"
                if skill_file.exists():
                    content = skill_file.read_text(encoding="utf-8")
                    if _skill_name_matches(content, item.name, tool_name):
                        return content
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

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("'\"")
                    if name.lower() == target_lower:
                        return True
    return False


# ---------------------------------------------------------------------------
# MCP tool execution
# ---------------------------------------------------------------------------

def call_tool(database, tool_id: str, arguments: dict) -> dict:
    """
    Execute a tool by ID with the given arguments.

    Routes to the correct execution method based on source type:
    - Skills: returns skill content for LLM execution
    - MCP (stdio/HTTP): proxies the call to the original server

    Args:
        database: ToolDatabase instance.
        tool_id: The tool's unique identifier.
        arguments: Arguments to pass to the tool.

    Returns:
        dict with keys: type ("skill"|"mcp_result"), result/content.

    Raises:
        ValueError: If tool not found.
        RuntimeError: If execution fails or source type not supported.
    """
    tool = database.get_tool_by_id(tool_id)
    if not tool:
        raise ValueError(f"Tool not found: {tool_id}")

    source_info = tool.get("source_info", {})
    source_type = source_info.get("source_type", "")

    # Skills — return content for LLM execution
    _SKILL_CONTENT_TYPES = {"skill", "skill_directory", "skill_file"}
    if source_type in _SKILL_CONTENT_TYPES:
        content = load_skill_content(tool["name"], source_info)
        return {
            "type": "skill",
            "name": tool["name"],
            "content": content,
            "instruction": "Follow the skill instructions above to complete the task."
        }

    # MCP tools — proxy to original server
    if "mcp" in source_type or source_type in ("streamableHttp", "sse", "skill_tool_stdio", "skill_tool_script"):
        result = proxy_mcp_call(tool, arguments, database)
        return {"type": "mcp_result", "result": result}

    raise RuntimeError(f"Execution not supported for source type: {source_type}")


def proxy_mcp_call(tool: dict, arguments: dict, database=None) -> dict:
    """
    Forward a tool call to the original MCP server.

    Supports both stdio and HTTP transports.
    """
    source_info = tool.get("source_info", {})
    original_name = source_info.get("original_name", tool["name"])
    source_type = source_info.get("source_type", "")

    fetcher = MCPFetcher()

    # stdio execution
    if source_type == "stdio" or source_info.get("command"):
        command = source_info.get("command")
        args = source_info.get("args", [])

        if not command and database:
            command, args = _lookup_stdio_config(source_info, database)

        if not command:
            raise RuntimeError(
                f"Cannot execute stdio tool '{original_name}': "
                f"command not found in source_info. Re-ingest the source to fix this."
            )

        return fetcher.call_stdio(command, args, original_name, arguments)

    # HTTP execution
    server_url = source_info.get("url", "")
    server_headers = source_info.get("headers", {})

    if not server_url and database:
        server_url, server_headers = _lookup_http_config(source_info, database)

    if not server_url:
        raise RuntimeError(
            f"Cannot execute tool '{original_name}': no URL or command found. "
            f"Source type '{source_type}' — re-ingest the source to fix this."
        )

    return _http_tool_call(server_url, server_headers, original_name, arguments)


def _lookup_stdio_config(source_info: dict, database) -> tuple:
    """Look up command+args from registered source configs for a stdio tool."""
    server = source_info.get("server", "")
    sources = database.get_all_sources()

    for src in sources:
        config = src.get("config", {})
        if config.get("path"):
            config_path = Path(os.path.expanduser(config["path"]))
            if not config_path.exists():
                continue
            try:
                raw = json.loads(config_path.read_text())
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
    server = source_info.get("server", "")
    sources = database.get_all_sources()

    for src in sources:
        config = src.get("config", {})
        if config.get("path"):
            config_path = Path(os.path.expanduser(config["path"]))
            if not config_path.exists():
                continue
            try:
                raw = json.loads(config_path.read_text())
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


def _get_or_create_session(server_url: str, server_headers: dict) -> tuple[httpx.Client, dict]:
    """
    Get or create a pooled MCP session for the given server URL.

    Reuses existing sessions (with their MCP session ID and persistent
    HTTP connection) to avoid the initialize+notify handshake on every call.
    Sessions expire after _SESSION_TTL seconds.

    Returns (client, headers) ready for tools/call requests.
    """
    now = time.monotonic()

    with _session_lock:
        cached = _sessions.get(server_url)
        if cached and (now - cached["created"]) < _SESSION_TTL:
            return cached["client"], cached["headers"]

        # Close old client if expired
        if cached:
            try:
                cached["client"].close()
            except Exception:
                pass

    # Create new session outside the lock (handshake is slow)
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **server_headers
    }

    client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))

    try:
        init_resp = client.post(
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
        )
        session_id = init_resp.headers.get("mcp-session-id")
        if session_id:
            h["mcp-session-id"] = session_id

        client.post(
            server_url, headers=h,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=10
        )
    except Exception as e:
        logger.warning("MCP session init failed for {}: {}", server_url, e)
        # Continue anyway — some servers don't require handshake
        client.close()
        client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))

    with _session_lock:
        _sessions[server_url] = {
            "session_id": h.get("mcp-session-id"),
            "headers": h,
            "client": client,
            "created": time.monotonic(),
        }

    logger.info("MCP session created for {} (session_id={})", server_url[:60], h.get("mcp-session-id", "none"))
    return client, h


def _http_tool_call(server_url: str, server_headers: dict,
                    tool_name: str, arguments: dict) -> dict:
    """Send a tools/call request to an HTTP MCP server with session pooling and result caching."""

    # Check result cache for read-only tools
    cached = _get_cached_result(tool_name, arguments)
    if cached is not None:
        return cached

    t0 = time.monotonic()
    client, h = _get_or_create_session(server_url, server_headers)

    try:
        resp = client.post(
            server_url, headers=h,
            json={
                "jsonrpc": "2.0", "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments}
            },
        )
        resp.raise_for_status()
    except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as e:
        # Session may have expired server-side — retry with fresh session
        logger.warning("MCP session error, retrying with fresh session: {}", e)
        with _session_lock:
            _sessions.pop(server_url, None)
        try:
            client.close()
        except Exception:
            pass
        client, h = _get_or_create_session(server_url, server_headers)
        resp = client.post(
            server_url, headers=h,
            json={
                "jsonrpc": "2.0", "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments}
            },
        )
        resp.raise_for_status()

    elapsed = time.monotonic() - t0
    logger.info("Tool {} executed in {:.1f}s", tool_name, elapsed)

    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        result = None
        for line in resp.text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if data:
                    try:
                        parsed = json.loads(data)
                        result = parsed.get("result", parsed)
                        break
                    except Exception:
                        continue
        if result is None:
            result = {"raw": resp.text}
    else:
        data = resp.json()
        result = data.get("result", data)

    # Cache read-only results
    _set_cached_result(tool_name, arguments, result)
    return result


def _resolve_env(val):
    """Resolve ${ENV_VAR} references in strings."""
    if isinstance(val, str):
        def replacer(m):
            return os.environ.get(m.group(1), "")
        return re.sub(r'\$\{(\w+)\}', replacer, val)
    return val
