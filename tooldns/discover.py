"""
discover.py — Auto-discover MCP servers from any URL.

Accepts URLs pointing to:
  - Smithery.ai server pages  → extracts package, generates npx command
  - npm package pages          → detects MCP packages, generates npx command
  - GitHub repositories        → reads README for install instructions
  - Direct HTTP MCP endpoints  → probes with JSON-RPC initialize
  - OpenAPI spec URLs          → (future) parses spec and creates tool entries

Usage:
    result = discover_from_url("https://smithery.ai/server/@modelcontextprotocol/server-github")
    if "error" not in result:
        pipeline.ingest_source(result["source_config"])
"""

import re
import json
from urllib.parse import urlparse
from typing import Optional


def discover_from_url(url: str) -> dict:
    """
    Detect source type from a URL and return an ingestable source config.

    Args:
        url: Any URL — Smithery, npm, GitHub, or direct MCP HTTP endpoint.

    Returns:
        dict with keys:
            source_config: Ready-to-pass dict for ingest_source()
            detected_type: Human-readable type detected
            message: Explanation of what was detected
          or:
            error: Error message if detection failed
    """
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    try:
        if "smithery.ai" in hostname:
            return _discover_smithery(url)

        if "npmjs.com" in hostname:
            return _discover_npm(url, parsed)

        if "github.com" in hostname:
            return _discover_github(url, parsed)

        if parsed.scheme in ("http", "https"):
            return _discover_http_mcp(url)

    except Exception as e:
        return {"error": f"Discovery failed: {e}"}

    return {"error": f"Unsupported URL format: {url}"}


# ---------------------------------------------------------------------------
# Smithery.ai
# ---------------------------------------------------------------------------

def _discover_smithery(url: str) -> dict:
    """
    Extract package name from a Smithery server page URL.

    Smithery URLs look like:
      https://smithery.ai/server/@modelcontextprotocol/server-github
      https://smithery.ai/server/brave-search

    We generate: npx -y @smithery/cli@latest run <package>
    """
    # Extract everything after /server/
    m = re.search(r"/server/(.+)$", url)
    if not m:
        return {"error": "Could not parse Smithery server URL — expected /server/<name>"}

    package = m.group(1).strip("/")
    # Smithery server slug (e.g. "@scope/name" or "plain-name")
    server_slug = package.split("/")[-1] if "/" in package and not package.startswith("@") else package
    name = re.sub(r"[^a-zA-Z0-9_\-]", "-", server_slug).strip("-")

    source_config = {
        "type": "mcp_stdio",
        "name": name,
        "command": "npx",
        "args": ["-y", "@smithery/cli@latest", "run", package, "--client", "claude"],
    }

    return {
        "source_config": source_config,
        "detected_type": "Smithery MCP Server",
        "message": f"Detected Smithery server '{package}' — will run via npx.",
    }


# ---------------------------------------------------------------------------
# npm
# ---------------------------------------------------------------------------

def _discover_npm(url: str, parsed) -> dict:
    """
    Extract package name from an npm URL and generate an npx command.

    URL format: https://www.npmjs.com/package/@scope/name
    """
    # /package/@scope/name  or  /package/name
    path = parsed.path  # e.g. /package/@modelcontextprotocol/server-github
    m = re.match(r"^/package/(.+)$", path)
    if not m:
        return {"error": "Could not parse npm package URL — expected /package/<name>"}

    package = m.group(1)
    slug = package.split("/")[-1]
    name = re.sub(r"[^a-zA-Z0-9_\-]", "-", slug).strip("-")

    # MCP packages typically have 'mcp' or 'server' in the name
    mcp_hint = any(kw in package.lower() for kw in ["mcp", "server-", "modelcontextprotocol"])

    source_config = {
        "type": "mcp_stdio",
        "name": name,
        "command": "npx",
        "args": ["-y", package],
    }

    msg = f"Detected npm package '{package}'."
    if not mcp_hint:
        msg += " Note: this may not be an MCP server — verify before adding."

    return {
        "source_config": source_config,
        "detected_type": "npm MCP Package",
        "message": msg,
    }


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def _discover_github(url: str, parsed) -> dict:
    """
    Detect MCP servers from GitHub repository URLs.

    Tries to read the README for npx/uvx install instructions.
    Falls back to a best-guess based on repo name.
    """
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return {"error": "Invalid GitHub URL — expected github.com/user/repo"}

    owner, repo = parts[0], parts[1]
    repo_clean = repo.lower()

    # Heuristic: is this likely an MCP server?
    is_mcp = any(kw in repo_clean for kw in ["mcp", "server", "modelcontextprotocol"])

    # Try to fetch README for install hints
    readme_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md"
    readme_fallback = f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.md"
    command, args, detected_transport = _extract_install_from_readme(readme_url, readme_fallback, repo)

    name = re.sub(r"[^a-zA-Z0-9_\-]", "-", repo_clean).strip("-")

    if command:
        source_config = {
            "type": "mcp_stdio",
            "name": name,
            "command": command,
            "args": args,
        }
        return {
            "source_config": source_config,
            "detected_type": "GitHub MCP Server (README detected)",
            "message": f"Found install command in README: {command} {' '.join(args)}",
        }

    # Fallback: guess npx install
    npm_package = f"@{owner}/{repo}" if not repo_clean.startswith("server-") else repo_clean
    source_config = {
        "type": "mcp_stdio",
        "name": name,
        "command": "npx",
        "args": ["-y", npm_package],
    }
    msg = f"GitHub repo '{owner}/{repo}'"
    if not is_mcp:
        msg += " — may not be an MCP server, verify the install command."
    else:
        msg += " — guessed npm package name, adjust if needed."

    return {
        "source_config": source_config,
        "detected_type": "GitHub Repository",
        "message": msg,
    }


def _extract_install_from_readme(readme_url: str, fallback_url: str, repo: str) -> tuple:
    """
    Fetch a README and extract npx/uvx/python MCP server install commands.

    Returns (command, args_list, transport) or (None, [], None).
    """
    try:
        import urllib.request
        for url in [readme_url, fallback_url]:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ToolsDNS/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    content = resp.read().decode("utf-8", errors="ignore")
                    return _parse_readme_for_command(content, repo)
            except Exception:
                continue
    except Exception:
        pass
    return None, [], None


def _parse_readme_for_command(content: str, repo: str) -> tuple:
    """
    Extract the first recognizable MCP install command from README text.

    Looks for patterns like:
      npx -y @scope/package
      uvx mcp-package
      python -m package_name
    """
    patterns = [
        # npx -y @scope/package  or  npx @scope/package
        (r'npx\s+(-y\s+)?(@[\w\-/]+|[\w\-]+(?:-mcp|mcp-[\w\-]+))', "npx"),
        # uvx package-name
        (r'uvx\s+([\w\-]+(?:-mcp|mcp-[\w\-]+|server[\w\-]*))', "uvx"),
        # python -m module
        (r'python3?\s+-m\s+([\w\._]+)', "python"),
    ]

    for pattern, cmd in patterns:
        m = re.search(pattern, content)
        if m:
            if cmd == "npx":
                # Reconstruct args
                flag = ["-y"] if m.group(1) else []
                pkg = m.group(2).strip()
                return "npx", flag + [pkg], "stdio"
            elif cmd == "uvx":
                return "uvx", [m.group(1).strip()], "stdio"
            elif cmd == "python":
                return "python3", ["-m", m.group(1).strip()], "stdio"

    return None, [], None


# ---------------------------------------------------------------------------
# Direct HTTP MCP endpoint
# ---------------------------------------------------------------------------

def _discover_http_mcp(url: str) -> dict:
    """
    Probe a URL as a direct HTTP MCP server endpoint.

    Sends a JSON-RPC initialize call. If it responds like an MCP server,
    returns an mcp_http source config. Otherwise returns an error.
    """
    import urllib.request
    import urllib.error

    probe_payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "tooldns-discover", "version": "1.0"}
        }
    }).encode()

    parsed = urlparse(url)
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "-", parsed.hostname or "mcp").strip("-")
    name = slug

    try:
        req = urllib.request.Request(
            url,
            data=probe_payload,
            headers={"Content-Type": "application/json", "User-Agent": "ToolsDNS/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="ignore")

        # Check if response looks like MCP
        is_mcp = False
        try:
            data = json.loads(body)
            if "result" in data and ("serverInfo" in data.get("result", {}) or
                                      "capabilities" in data.get("result", {})):
                is_mcp = True
            elif "jsonrpc" in data:
                is_mcp = True
        except Exception:
            pass

        if is_mcp or status in (200, 201, 202):
            source_config = {
                "type": "mcp_http",
                "name": name,
                "url": url,
            }
            return {
                "source_config": source_config,
                "detected_type": "Direct HTTP MCP Server",
                "message": f"Server at {url} responded to MCP initialize — ready to connect.",
            }

        return {"error": f"URL responded (HTTP {status}) but doesn't look like an MCP server."}

    except urllib.error.HTTPError as e:
        if e.code in (400, 404, 405):
            # Server is alive but method not supported — still try it
            source_config = {
                "type": "mcp_http",
                "name": name,
                "url": url,
            }
            return {
                "source_config": source_config,
                "detected_type": "HTTP MCP Server (unverified)",
                "message": f"Server at {url} is reachable. Could be an MCP server — adding as HTTP source.",
            }
        return {"error": f"HTTP error {e.code} probing {url}"}

    except Exception as e:
        return {"error": f"Could not reach {url}: {e}"}
