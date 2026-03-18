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
import httpx
from pathlib import Path
from typing import Optional

from tooldns.config import logger, TOOLDNS_HOME
from tooldns.fetcher import MCPFetcher


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


def _http_tool_call(server_url: str, server_headers: dict,
                    tool_name: str, arguments: dict) -> dict:
    """Send a tools/call request to an HTTP MCP server."""
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
        for line in resp.text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if data:
                    try:
                        parsed = json.loads(data)
                        return parsed.get("result", parsed)
                    except Exception:
                        continue
        return {"raw": resp.text}
    else:
        data = resp.json()
        return data.get("result", data)


def _resolve_env(val):
    """Resolve ${ENV_VAR} references in strings."""
    if isinstance(val, str):
        def replacer(m):
            return os.environ.get(m.group(1), "")
        return re.sub(r'\$\{(\w+)\}', replacer, val)
    return val
