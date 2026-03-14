"""
mcp_server.py — ToolDNS as an MCP server.

This makes ToolDNS itself a tool that LLMs can use. Instead of
nanobot loading 135+ Composio tools into every prompt, it loads
ONE tool: `tooldns_search`. When the LLM needs a tool, it searches
ToolDNS, gets the right one, and ToolDNS proxies the execution.

Usage in nanobot's config.json:
    "mcpServers": {
        "tooldns": {
            "command": "python3",
            "args": ["-m", "tooldns.mcp_server"]
        }
    }

This gives the LLM three tools:
    1. search_tools — Find tools by natural language description
    2. get_tool    — Get full details (schema, skill content)
    3. call_tool   — Execute a tool via its original MCP server

Token savings example:
    - Without ToolDNS: 135 Composio tools = ~16,000 tokens per prompt
    - With ToolDNS:    3 tools × ~40 tokens = ~120 tokens per prompt
    - Savings: ~99% reduction in tool schema tokens
"""

import sys
import json
import logging

logger = logging.getLogger("tooldns.mcp")

# ---------------------------------------------------------------------------
# MCP protocol helpers
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_tools",
        "description": (
            "Search the ToolDNS index by natural language. "
            "Describe what you need in plain English and get back "
            "the most relevant tools with confidence scores. "
            "Example: 'send an email' → GMAIL_SEND_EMAIL (75%)"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language description of what you need"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results to return (default: 3)",
                    "default": 3
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_tool",
        "description": (
            "Get full details for a specific tool by ID. "
            "Returns the complete input schema, description, and "
            "for skills, the full instructions the LLM should follow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_id": {
                    "type": "string",
                    "description": "The tool ID from search results"
                }
            },
            "required": ["tool_id"]
        }
    },
    {
        "name": "call_tool",
        "description": (
            "Execute a tool via ToolDNS. For MCP tools, forwards the "
            "call to the original MCP server. For skills, returns the "
            "skill instructions to follow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_id": {
                    "type": "string",
                    "description": "The tool ID to execute"
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments to pass to the tool",
                    "default": {}
                }
            },
            "required": ["tool_id"]
        }
    }
]


def _make_response(id: int, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _make_error(id: int, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Tool handlers — connect to the ToolDNS API on localhost
# ---------------------------------------------------------------------------

def _api_request(method: str, path: str, body: dict = None) -> dict:
    """Make a request to the ToolDNS HTTP API."""
    import httpx
    from tooldns.config import settings

    url = f"http://127.0.0.1:{settings.port}{path}"
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    try:
        if method == "GET":
            resp = httpx.get(url, headers=headers, timeout=30)
        else:
            resp = httpx.post(url, headers=headers, json=body, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def handle_search_tools(args: dict) -> list[dict]:
    """Handle search_tools calls."""
    query = args.get("query", "")
    top_k = args.get("top_k", 3)

    result = _api_request("POST", "/v1/search", {
        "query": query, "top_k": top_k
    })

    if "error" in result:
        return [{"type": "text", "text": f"Search error: {result['error']}"}]

    results = result.get("results", [])
    if not results:
        return [{"type": "text", "text": f"No tools found for: {query}"}]

    lines = [f"Found {len(results)} tool(s) for \"{query}\":\n"]
    for r in results:
        lines.append(f"• **{r['name']}** (ID: `{r['id']}`, {r['confidence']:.0%})")
        lines.append(f"  {r['description'][:120]}")
        how = r.get("how_to_call", {})
        if how.get("instruction"):
            lines.append(f"  → {how['instruction']}")
        lines.append("")

    lines.append("Use `get_tool` for full schema or `call_tool` to execute.")
    return [{"type": "text", "text": "\n".join(lines)}]


def handle_get_tool(args: dict) -> list[dict]:
    """Handle get_tool calls."""
    tool_id = args.get("tool_id", "")
    result = _api_request("GET", f"/v1/tool/{tool_id}")

    if "error" in result:
        return [{"type": "text", "text": f"Error: {result['error']}"}]

    parts = []

    # Main info
    parts.append(f"# {result['name']}\n")
    parts.append(f"{result.get('description', '')}\n")

    # Input schema
    schema = result.get("input_schema", {})
    if schema:
        parts.append(f"## Input Schema\n```json\n{json.dumps(schema, indent=2)}\n```\n")

    # How to call
    how = result.get("how_to_call", {})
    if how:
        parts.append(f"## How to Call\n{how.get('instruction', '')}\n")

    # Skill content
    skill_content = result.get("skill_content", "")
    if skill_content:
        parts.append(f"## Skill Instructions\n{skill_content}\n")

    return [{"type": "text", "text": "\n".join(parts)}]


def handle_call_tool(args: dict) -> list[dict]:
    """Handle call_tool calls."""
    tool_id = args.get("tool_id", "")
    arguments = args.get("arguments", {})

    result = _api_request("POST", "/v1/call", {
        "tool_id": tool_id, "arguments": arguments
    })

    if "error" in result:
        return [{"type": "text", "text": f"Execution error: {result['error']}"}]

    result_type = result.get("type", "unknown")

    if result_type == "skill":
        content = result.get("content", "")
        instruction = result.get("instruction", "")
        return [{"type": "text", "text": f"{instruction}\n\n{content}"}]

    if result_type == "mcp_result":
        return [{"type": "text", "text": json.dumps(result.get("result", {}), indent=2)}]

    return [{"type": "text", "text": json.dumps(result, indent=2)}]


HANDLERS = {
    "search_tools": handle_search_tools,
    "get_tool": handle_get_tool,
    "call_tool": handle_call_tool,
}


# ---------------------------------------------------------------------------
# Main stdio loop
# ---------------------------------------------------------------------------

def run():
    """Run the ToolDNS MCP server on stdio."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr  # Logs go to stderr, protocol goes to stdout
    )

    logger.info("ToolDNS MCP server starting on stdio")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")

        # Handle MCP protocol messages
        if method == "initialize":
            resp = _make_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "tooldns", "version": "1.0.0"}
            })
            _send(resp)

        elif method == "notifications/initialized":
            pass  # No response needed

        elif method == "tools/list":
            resp = _make_response(msg_id, {"tools": TOOLS})
            _send(resp)

        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            handler = HANDLERS.get(tool_name)
            if handler:
                try:
                    content = handler(arguments)
                    resp = _make_response(msg_id, {
                        "content": content,
                        "isError": False
                    })
                except Exception as e:
                    resp = _make_response(msg_id, {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True
                    })
            else:
                resp = _make_error(msg_id, -32601, f"Unknown tool: {tool_name}")

            _send(resp)

        elif method == "ping":
            resp = _make_response(msg_id, {})
            _send(resp)

        elif msg_id is not None:
            resp = _make_error(msg_id, -32601, f"Unknown method: {method}")
            _send(resp)


def _send(msg: dict):
    """Send a JSON-RPC message to stdout."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    run()
