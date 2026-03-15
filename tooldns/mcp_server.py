"""
mcp_server.py — ToolsDNS as an MCP server (fastmcp edition).

Exposes ToolsDNS to any MCP-capable agent as five tools:
    1. search_tools      — find tools by natural language
    2. get_tool          — get full schema + skill instructions
    3. call_tool         — execute a tool through ToolsDNS
    4. register_mcp_server — add a new MCP server on the fly
    5. create_skill      — create a new skill file

Plus two live MCP resources:
    tooldns://tools   — browse all indexed tools
    tooldns://sources — list all registered MCP server sources

Usage in nanobot / openclaw / mcporter config:
    "tooldns": {
        "command": "python3",
        "args": ["-m", "tooldns.mcp_server"]
    }

Benefits of fastmcp over hand-rolled JSON-RPC:
    - Automatic MCP protocol handling (initialize, ping, tool list)
    - Type-safe tool definitions via Python type hints
    - Proper error propagation with McpError
    - Built-in progress notifications
    - Auto-generated input schema from function signature
    - Stdio transport handled by the library
"""

import contextvars
import json
import os
from typing import Optional

import httpx
from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError

from tooldns.config import settings

# Per-request API key — set by MCPKeyMiddleware from the incoming Bearer token.
# MCP tools read this to forward the caller's key to internal API calls so
# usage and token counts are credited to the right sub-key.
_request_api_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_request_api_key", default=""
)

# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="tooldns",
    version="2.0.0",
    instructions=(
        "ToolsDNS gives you semantic search over 100+ MCP tools. "
        "Instead of loading every tool schema into your context, call "
        "search_tools() first to find the right tool, then call_tool() "
        "to execute it. This saves thousands of tokens per request.\n\n"
        "Workflow:\n"
        "1. search_tools(query='what you need') → get tool IDs + schemas\n"
        "2. call_tool(tool_id='...', arguments={...}) → execute it\n"
        "Use get_tool() only if you need the full schema or skill instructions."
    ),
)


# ---------------------------------------------------------------------------
# Shared async HTTP client
# ---------------------------------------------------------------------------

_client: Optional[httpx.AsyncClient] = None


def _api_base_url() -> str:
    """Resolve base URL — supports TOOLDNS_API_URL for remote connections."""
    url = os.environ.get("TOOLDNS_API_URL", "").rstrip("/")
    return url or f"http://127.0.0.1:{settings.port}"


def _api_key() -> str:
    """Resolve API key — prefers TOOLDNS_API_KEY env var over settings."""
    return os.environ.get("TOOLDNS_API_KEY", "") or settings.api_key


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=_api_base_url(),
            headers={"Authorization": f"Bearer {_api_key()}"},
            timeout=httpx.Timeout(60.0),
        )
    return _client


async def _api(method: str, path: str, body: dict | None = None) -> dict:
    """Make a request to the ToolsDNS HTTP API.

    Uses the per-request caller key (set by MCPKeyMiddleware) so that usage
    and token savings are credited to the right sub-key, not the admin key.
    Falls back to the admin key when called outside an MCP request context.
    """
    client = await _get_client()
    # Prefer the caller's key captured from the incoming MCP request
    caller_key = _request_api_key.get()
    headers = {"Authorization": f"Bearer {caller_key}"} if caller_key else {}
    try:
        if method == "GET":
            resp = await client.get(path, headers=headers)
        else:
            resp = await client.post(path, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        raise ToolError(f"ToolsDNS API error {e.response.status_code}: {e.response.text[:200]}")
    except httpx.ConnectError:
        raise ToolError(
            "Cannot connect to ToolsDNS (http://127.0.0.1:8787). "
            "Is the service running? Check: systemctl status toolsdns"
        )
    except Exception as e:
        raise ToolError(f"ToolsDNS API error: {e}")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_tools(
    query: str,
    top_k: int = 3,
    ctx: Optional[Context] = None,
) -> str:
    """
    Search the ToolsDNS index by natural language description.

    Describe what you need and get back the most relevant tools with
    confidence scores and input schemas. Use the returned tool_id with
    call_tool() to execute a tool.

    Examples:
      search_tools("send an email") → GMAIL_SEND_EMAIL
      search_tools("create github issue") → GITHUB_CREATE_ISSUE
      search_tools("search the web") → BRAVE_SEARCH

    Args:
        query: Natural language description of what you need to do.
        top_k: Max results to return (default: 3, max: 10).
    """
    if ctx:
        await ctx.info(f"Searching for: {query!r} (top_k={min(top_k, 10)})")

    result = await _api("POST", "/v1/search", {"query": query, "top_k": min(top_k, 10)})

    results = result.get("results", [])
    if not results:
        hint = result.get("hint", "")
        if hint:
            return f"No tools found for: {query!r}\n\n💡 HINT FOR AI: {hint}"
        return f"No tools found for: {query!r}\nTry rephrasing or use a different keyword."

    total = result.get("total_tools_indexed", 0)
    tokens_saved = result.get("tokens_saved", 0)
    search_ms = result.get("search_time_ms", 0)

    if ctx:
        await ctx.info(f"Found {len(results)} result(s) from {total} indexed tools in {search_ms:.0f}ms")

    lines = [
        f"Found {len(results)} tool(s) for {query!r} "
        f"(searched {total} tools in {search_ms:.0f}ms, ~{tokens_saved:,} tokens saved):\n"
    ]
    for r in results:
        schema = r.get("input_schema", {})
        lines.append(f"• **{r['name']}** (ID: `{r['id']}`, confidence: {r['confidence']:.0%})")
        lines.append(f"  {r['description'][:140]}")
        if schema:
            lines.append(f"  Schema: {json.dumps(schema)[:300]}")
        lines.append("")

    all_mcp = all(r.get("how_to_call", {}).get("type") == "mcp" for r in results)
    if all_mcp:
        lines.append(
            "These are MCP tools — call them with `call_tool(tool_id, arguments)`. "
            "Skip `get_tool` unless you need the full schema."
        )
    else:
        lines.append("Use `get_tool` for skills (need full instructions), or `call_tool` to execute directly.")

    hint = result.get("hint")
    if hint:
        lines.append(f"\n💡 HINT FOR AI: {hint}")

    return "\n".join(lines)


@mcp.tool()
async def get_tool(
    tool_id: str,
    ctx: Optional[Context] = None,
) -> str:
    """
    Get full details for a specific tool by ID.

    Returns the complete input schema, description, calling instructions,
    and for skills, the full markdown content the agent should follow.

    Args:
        tool_id: The tool ID from search_tools results (e.g. 'tooldns__GMAIL_SEND_EMAIL').
    """
    if ctx:
        await ctx.info(f"Fetching tool details for: {tool_id!r}")

    result = await _api("GET", f"/v1/tool/{tool_id}")

    parts = [f"# {result['name']}\n", f"{result.get('description', '')}\n"]

    schema = result.get("input_schema", {})
    if schema:
        parts.append(f"## Input Schema\n```json\n{json.dumps(schema, indent=2)}\n```\n")

    how = result.get("how_to_call", {})
    if how:
        parts.append(f"## How to Call\n{how.get('instruction', '')}\n")
        if how.get("server"):
            parts.append(f"Server: `{how['server']}`  Tool: `{how.get('tool_name', result['name'])}`\n")

    skill_content = result.get("skill_content", "")
    if skill_content:
        parts.append(f"## Skill Instructions\n{skill_content}\n")

    return "\n".join(parts)


@mcp.tool()
async def call_tool(
    tool_id: str,
    arguments: Optional[dict] = None,
    ctx: Optional[Context] = None,
) -> str:
    """
    Execute a tool via ToolsDNS.

    For MCP tools, forwards the call to the original MCP server and returns
    the result. For skills, returns the skill instructions to follow.

    Args:
        tool_id: The tool ID to execute (from search_tools results).
        arguments: Arguments to pass to the tool as a JSON object.
    """
    if ctx:
        await ctx.info(f"Calling tool: {tool_id!r} with arguments: {json.dumps(arguments or {})[:200]}")

    result = await _api("POST", "/v1/call", {
        "tool_id": tool_id,
        "arguments": arguments or {},
    })

    result_type = result.get("type", "unknown")

    if ctx:
        await ctx.info(f"Tool returned result of type: {result_type!r}")

    if result_type == "skill":
        content = result.get("content", "")
        instruction = result.get("instruction", "")
        return f"{instruction}\n\n{content}".strip()

    if result_type == "mcp_result":
        inner = result.get("result", {})
        # Extract text content if it's an MCP response
        if isinstance(inner, dict) and "content" in inner:
            parts = []
            for block in inner["content"]:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            if parts:
                return "\n".join(parts)
        return json.dumps(inner, indent=2)

    return json.dumps(result, indent=2)


@mcp.tool()
async def register_mcp_server(
    name: str,
    command: Optional[str] = None,
    args: Optional[list[str]] = None,
    url: Optional[str] = None,
    headers: Optional[dict] = None,
    env_vars: Optional[dict] = None,
    ctx: Optional[Context] = None,
) -> str:
    """
    Register a new MCP server into ToolsDNS.

    Saves credentials to ~/.tooldns/.env, adds the server to
    ~/.tooldns/config.json, and indexes its tools immediately so they
    can be found via search_tools.

    Args:
        name: Short identifier for the server (e.g. 'github', 'slack').
        command: Executable for stdio servers (e.g. 'npx', 'python3').
        args: Arguments for stdio servers (e.g. ['-y', '@mcp/github']).
        url: URL for HTTP/SSE MCP servers.
        headers: HTTP headers for HTTP servers (e.g. auth tokens).
        env_vars: Environment variables to save (e.g. {'GITHUB_TOKEN': 'ghp_...'}).
    """
    if not command and not url:
        raise ToolError("Either command or url is required.")

    if ctx:
        await ctx.info(f"Registering MCP server: {name!r}")

    result = await _api("POST", "/v1/register-mcp", {
        "name": name,
        "command": command,
        "args": args or [],
        "url": url,
        "headers": headers,
        "env_vars": env_vars,
        "ingest": True,
    })

    if ctx:
        await ctx.info(f"Server '{name}' registered — {result.get('tools_indexed', 0)} tools indexed")

    lines = [f"✅ MCP server '{result['name']}' registered ({result['transport']})"]
    if result.get("env_vars_saved"):
        lines.append(f"  Credentials saved: {', '.join(result['env_vars_saved'])}")
    lines.append(f"  Tools indexed: {result['tools_indexed']}")
    if result.get("ingest_error"):
        lines.append(f"  ⚠ Indexing warning: {result['ingest_error']}")
    lines.append(f"  Config: {result['config_file']}")
    return "\n".join(lines)


@mcp.tool()
async def create_skill(
    name: str,
    description: str,
    content: str,
    skill_path: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> str:
    """
    Create a new skill file in the ToolsDNS skills directory.

    A skill is a markdown file that teaches the agent how to call an API
    or perform a multi-step task. Indexed immediately after creation so
    it's findable via search_tools.

    Args:
        name: Skill name used as the folder name (e.g. 'send-report').
        description: One-line description of what the skill does.
        content: Full markdown content of the SKILL.md file.
        skill_path: Optional path to a custom skills directory.
    """
    if ctx:
        await ctx.info(f"Creating skill: {name!r} — {description}")

    result = await _api("POST", "/v1/skills", {
        "name": name,
        "description": description,
        "content": content,
        "skill_path": skill_path,
        "ingest": True,
    })

    if ctx:
        await ctx.info(f"Skill '{name}' created at {result.get('file', '?')}")

    return "\n".join([
        f"✅ Skill '{result['name']}' created",
        f"  File: {result['file']}",
        f"  Tools indexed: {result['tools_indexed']}",
        f"  Find it with: search_tools({description!r})",
    ])


@mcp.tool()
async def read_skill(
    name: str,
    ctx: Optional[Context] = None,
) -> str:
    """
    Read a skill's current SKILL.md and any tool scripts inside its folder.

    Use this before editing a skill to see what's currently there.
    Returns the skill markdown content and the source of any tool scripts.

    Args:
        name: The skill folder name (e.g. 'daily-standup', 'send-report').
    """
    if ctx:
        await ctx.info(f"Reading skill: {name!r}")

    result = await _api("GET", f"/v1/skills/{name}")

    parts = [f"# Skill: {result['name']}\n", f"**File:** `{result['file']}`\n"]
    parts.append(f"## SKILL.md\n```markdown\n{result['content']}\n```\n")

    scripts = result.get("tool_scripts", [])
    if scripts:
        parts.append(f"## Tool Scripts ({len(scripts)})\n")
        for s in scripts:
            parts.append(f"### {s['name']} ({s['size']} bytes)\n")
            if s.get("content"):
                parts.append(f"```python\n{s['content'][:3000]}\n```\n")
    else:
        parts.append("*No tool scripts in this skill folder.*\n")

    return "\n".join(parts)


@mcp.tool()
async def update_skill(
    name: str,
    content: str,
    script_name: Optional[str] = None,
    script_content: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> str:
    """
    Safely update a skill's SKILL.md and/or a tool script inside its folder.

    Always creates .bak backups before writing. Re-indexes immediately so
    the updated skill is findable via search_tools. Validates paths to
    prevent directory traversal.

    Args:
        name: Skill folder name (e.g. 'daily-standup').
        content: New SKILL.md content (full markdown, including frontmatter).
        script_name: Optional .py filename to update (e.g. 'tool.py').
        script_content: New Python content for the tool script.
    """
    if ctx:
        await ctx.info(f"Updating skill: {name!r}")

    body: dict = {"content": content}
    if script_name:
        body["script_name"] = script_name
    if script_content:
        body["script_content"] = script_content

    result = await _api("PUT", f"/v1/skills/{name}", body)

    if ctx:
        await ctx.info(
            f"Skill '{name}' updated — {result.get('tools_indexed', 0)} tools re-indexed"
        )

    lines = [f"✅ Skill '{name}' updated"]
    updated = result.get("updated_files", [])
    if updated:
        lines.append(f"  Files updated: {', '.join(updated)}")
        lines.append(f"  Backups: {', '.join(f + '.bak' for f in updated)}")
    lines.append(f"  Tools re-indexed: {result['tools_indexed']}")
    lines.append(f"  Find it with: search_tools({name!r})")
    return "\n".join(lines)


@mcp.tool()
async def list_tools(
    category: Optional[str] = None,
    source: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> str:
    """
    List all tools indexed in ToolsDNS, optionally filtered by category or source.

    Returns a summary of every indexed tool. For large indexes, use
    search_tools(query) instead to find specific tools by description.

    Args:
        category: Optional category filter (e.g. 'GitHub', 'Email', 'Files').
        source: Optional source/server filter (e.g. 'composio', 'browser-use').
    """
    if ctx:
        await ctx.info("Listing indexed tools...")

    params = []
    if category:
        params.append(f"category={category}")
    if source:
        params.append(f"source={source}")
    qs = ("?" + "&".join(params)) if params else ""

    result = await _api("GET", f"/v1/tools{qs}")
    tools = result.get("tools", [])
    total = result.get("total", len(tools))

    if not tools:
        return "No tools indexed yet. Add sources via register_mcp_server()."

    lines = [f"## {total} Tools Indexed in ToolsDNS\n"]
    lines.append("Use `search_tools(query)` to find tools by what you want to do.\n")

    # Group by source for readability
    by_source: dict[str, list] = {}
    for t in tools:
        src = t.get("source", "unknown")
        by_source.setdefault(src, []).append(t)

    for src, src_tools in sorted(by_source.items()):
        lines.append(f"\n### {src} ({len(src_tools)} tools)")
        for t in src_tools[:20]:  # cap per source to avoid huge output
            lines.append(f"- **{t['name']}**: {t.get('description', '')[:100]}")
        if len(src_tools) > 20:
            lines.append(f"  ... and {len(src_tools) - 20} more — use search_tools() to find specific ones")

    return "\n".join(lines)


@mcp.tool()
async def list_skills(ctx: Optional[Context] = None) -> str:
    """
    List all skills available in ToolsDNS.

    Call this when the user asks "what skills do you have", "show me your skills",
    "what can you do", or any similar question about capabilities.

    Returns a formatted list of every skill with its name and description.
    Use read_skill(name) to get the full instructions for a specific skill.
    """
    result = await _api("GET", "/v1/skills")
    skills = result.get("skills", [])

    if not skills:
        return (
            "No skills found. Skills live in ~/.tooldns/skills/ as SKILL.md files.\n"
            "You can create one with create_skill()."
        )

    lines = [f"## {len(skills)} Skill(s) Available\n"]
    for s in skills:
        name = s.get("name", "?")
        desc = s.get("description", "No description")[:120]
        lines.append(f"• **{name}** — {desc}")

    lines.append(f"\nUse `read_skill(name)` to get full instructions for any skill.")
    lines.append("Use `search_tools(query)` to find tools by what you want to do.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("tooldns://tools")
async def tools_resource() -> str:
    """Browse all tools indexed in ToolsDNS. Returns a summary list."""
    result = await _api("POST", "/v1/search", {"query": "tool", "top_k": 50})
    results = result.get("results", [])
    total = result.get("total_tools_indexed", 0)
    lines = [f"# ToolsDNS — {total} tools indexed\n"]
    for r in results:
        lines.append(f"- **{r['name']}** ({r['source']}): {r['description'][:100]}")
    return "\n".join(lines)


@mcp.resource("tooldns://sources")
async def sources_resource() -> str:
    """All registered MCP server sources in ToolsDNS."""
    result = await _api("GET", "/health")
    sources = result.get("sources", [])
    total_tools = result.get("tools_indexed", 0)
    lines = [f"# ToolsDNS Sources — {total_tools} tools across {len(sources)} source(s)\n"]
    if sources:
        for src in sources:
            if isinstance(src, dict):
                name = src.get("name", str(src))
                transport = src.get("transport", "")
                tool_count = src.get("tools_indexed", "?")
                lines.append(f"- **{name}** ({transport}): {tool_count} tools")
            else:
                lines.append(f"- {src}")
    else:
        lines.append("No source details available. Use search_tools() to explore indexed tools.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    """Run the ToolsDNS MCP server (transport selected via TOOLDNS_MCP_TRANSPORT env var)."""
    transport = os.environ.get("TOOLDNS_MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    run()
