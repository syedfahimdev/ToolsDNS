"""
integrate.py — Wire ToolDNS into nanobot / openclaw agents.

Interactive wizard that:
    1. Detects nanobot/openclaw and shows their MCP servers
    2. Adds 'tooldns' MCP server to their config (with confirmation)
    3. Migrates heavy HTTP MCP servers to ~/.tooldns/config.json
    4. Appends ToolDNS usage instructions to AGENTS.md
"""

import json
import copy
from pathlib import Path

from tooldns.config import TOOLDNS_HOME

# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------

TOOLDNS_AGENT_INSTRUCTIONS = """
## 🔍 ToolDNS — Smart Tool Discovery (IMPORTANT)

**You have access to 130+ external tools** (Composio, skills, custom tools) via ToolDNS.
When asked "what tools do you have", ALWAYS mention ToolDNS and its capabilities.

Use these 3 MCP tools to find and execute any external tool on demand:

| MCP Tool | Purpose |
|----------|---------|
| `mcp_tooldns_search_tools` | Search by description → "send email", "browse website", etc. |
| `mcp_tooldns_get_tool` | Get full schema + skill instructions for a tool |
| `mcp_tooldns_call_tool` | Execute the tool — proxies to original MCP server |

### Workflow
1. **Need a tool?** → `mcp_tooldns_search_tools(query="what you need")`
2. **Execute directly** → `mcp_tooldns_call_tool(tool_id="...", arguments={...})` — search results include the schema, so skip `get_tool` for MCP tools
3. **Only use `mcp_tooldns_get_tool`** if the tool type is `skill` (needs full instructions) or you need more schema detail

### When to Use
- Email, calendar, CRM, spreadsheets, or any Composio tool
- Browser automation, web scraping
- Any task where you're unsure if a tool exists — **search first!**
- **Don't use** for your built-in tools (file ops, exec, cron, etc.)
""".strip()


KNOWN_FRAMEWORKS = [
    {
        "name": "nanobot",
        "config_path": Path.home() / ".nanobot" / "config.json",
        "agents_path": Path.home() / ".nanobot" / "workspace" / "AGENTS.md",
        "mcp_key": "tools.mcpServers",
        "desc": "Nanobot AI agent framework",
    },
    {
        "name": "openclaw",
        "config_path": Path.home() / ".openclaw" / "workspace" / "config" / "mcporter.json",
        "agents_path": Path.home() / ".openclaw" / "workspace" / "AGENTS.md",
        "mcp_key": "mcpServers",
        "desc": "OpenClaw agent framework",
    },
]

# Servers that should stay in the agent config (lightweight / local)
KEEP_SERVERS = {"tooldns"}


# -----------------------------------------------------------------------
# Main entry
# -----------------------------------------------------------------------

def run_integrate():
    """
    Interactive wizard to wire ToolDNS into nanobot/openclaw.

    Steps:
        1. Detect frameworks and show current MCP servers
        2. Add 'tooldns' MCP server to framework config
        3. Migrate heavy MCP servers to ~/.tooldns/config.json
        4. Update AGENTS.md with ToolDNS instructions
    """
    print("🔌 ToolDNS Integration Wizard\n")
    print("   This will configure your AI agents to use ToolDNS")
    print("   for smart tool discovery instead of loading all tools.\n")

    detected = [fw for fw in KNOWN_FRAMEWORKS if fw["config_path"].exists()]

    if not detected:
        print("   ❌ No supported frameworks found.")
        print("   Looked for: nanobot (~/.nanobot/config.json)")
        print("               openclaw (~/.openclaw/workspace/config/mcporter.json)")
        return

    for fw in detected:
        print(f"   Found: {fw['name']} — {fw['desc']}")
        print(f"          Config: {fw['config_path']}")
        if fw["agents_path"].exists():
            print(f"          Agents: {fw['agents_path']}")
        print()

    for fw in detected:
        _integrate_framework(fw)

    print("\n🎉 Integration complete!\n")
    print("   Next steps:")
    print("   1. Start ToolDNS:  python3 -m tooldns.cli serve")
    print("   2. Start your agent (nanobot/openclaw)")
    print("   3. The agent now uses 'search_tools' to find any tool!\n")


# -----------------------------------------------------------------------
# Per-framework integration
# -----------------------------------------------------------------------

def _integrate_framework(fw: dict):
    """Run the integration wizard for a single framework."""
    name = fw["name"]
    config_path = fw["config_path"]
    agents_path = fw["agents_path"]
    mcp_key = fw["mcp_key"]

    print(f"{'━' * 42}")
    print(f"   Integrating: {name}")
    print(f"{'━' * 42}\n")

    # Load config
    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"   ❌ Cannot read config: {e}")
        return

    # Navigate to MCP servers section
    mcp_section = raw_config
    keys = mcp_key.split(".")
    for key in keys:
        mcp_section = mcp_section.get(key, {})

    if not mcp_section:
        print(f"   ⚠ No MCP servers found at '{mcp_key}'")
        return

    # Show current servers
    print(f"   Current MCP servers in {name}:")
    for srv_name, srv_config in mcp_section.items():
        srv_type = srv_config.get("type", "stdio")
        if srv_config.get("command"):
            srv_type = f"stdio ({srv_config['command']})"
        print(f"     • {srv_name} ({srv_type})")
    print()

    # Step 1: Add tooldns MCP server
    _step_add_tooldns(mcp_section, raw_config, config_path, keys)

    # Step 2: Migrate heavy servers
    _step_migrate_servers(mcp_section, raw_config, config_path, keys, mcp_key)

    # Step 3: Update AGENTS.md
    _step_update_agents(agents_path)


# -----------------------------------------------------------------------
# Step 1: Add tooldns MCP server
# -----------------------------------------------------------------------

def _step_add_tooldns(mcp_section, raw_config, config_path, keys):
    """Add the tooldns MCP server entry to the framework config."""
    if "tooldns" in mcp_section:
        print("   ✅ ToolDNS MCP server already in config\n")
        return

    choice = input("   Add 'tooldns' MCP server to config? [Y/n]: ").strip().lower()
    if choice not in ("", "y", "yes"):
        print("   ⏩ Skipped\n")
        return

    mcp_section["tooldns"] = {
        "command": "python3",
        "args": ["-m", "tooldns.mcp_server"]
    }
    _save_config(raw_config, config_path, keys, mcp_section)
    print("   ✅ Added 'tooldns' MCP server\n")


# -----------------------------------------------------------------------
# Step 2: Migrate heavy MCP servers
# -----------------------------------------------------------------------

def _step_migrate_servers(mcp_section, raw_config, config_path, keys, mcp_key):
    """Identify and migrate heavy HTTP MCP servers to tooldns config."""
    migratable = {}
    for srv_name, srv_config in list(mcp_section.items()):
        if srv_name in KEEP_SERVERS:
            continue
        srv_type = srv_config.get("type", "")
        if srv_type in ("streamableHttp", "sse") or srv_config.get("url"):
            migratable[srv_name] = srv_config

    if not migratable:
        print("   ✅ No heavy MCP servers to migrate\n")
        return

    print(f"   📦 Found {len(migratable)} heavy MCP server(s) to migrate:")
    for srv_name in migratable:
        print(f"     • {srv_name}")
    print()
    print("   These load many tools into every prompt.")
    print("   ToolDNS can search them on-demand instead.\n")

    choice = input("   Migrate to ~/.tooldns/config.json? [Y/n]: ").strip().lower()
    if choice not in ("", "y", "yes"):
        print("   ⏩ Skipped migration\n")
        return

    # Load tooldns config
    tooldns_config_path = TOOLDNS_HOME / "config.json"
    tooldns_config = {}
    if tooldns_config_path.exists():
        try:
            tooldns_config = json.loads(tooldns_config_path.read_text())
        except Exception:
            pass

    tooldns_mcp = tooldns_config.setdefault("mcpServers", {})
    migrated = []

    for srv_name, srv_config in migratable.items():
        sanitized = _sanitize_credentials(srv_name, srv_config)
        tooldns_mcp[srv_name] = sanitized
        migrated.append(srv_name)

    tooldns_config_path.write_text(json.dumps(tooldns_config, indent=2))
    print(f"\n   ✅ Migrated {len(migrated)} server(s) to {tooldns_config_path}")

    # Remove from agent config
    choice = input(f"\n   Remove migrated servers from {config_path.name}? [Y/n]: ").strip().lower()
    if choice in ("", "y", "yes"):
        for srv_name in migrated:
            mcp_section.pop(srv_name, None)
        _save_config(raw_config, config_path, keys, mcp_section)
        print(f"   ✅ Removed {len(migrated)} server(s) from {config_path.name}")
        for srv_name in migrated:
            print(f"     ✗ {srv_name} → ~/.tooldns/config.json")
        print()
    else:
        print("   ⏩ Kept in agent config (they'll still load into context)\n")


# -----------------------------------------------------------------------
# Step 3: Update AGENTS.md
# -----------------------------------------------------------------------

def _step_update_agents(agents_path: Path):
    """Add or replace ToolDNS instructions in the agent's AGENTS.md file."""
    if not agents_path.exists():
        print(f"   ⚠ No AGENTS.md found at {agents_path}")
        print(f"   You'll need to manually add ToolDNS instructions.\n")
        return

    content = agents_path.read_text(encoding="utf-8")
    has_tooldns = "ToolDNS" in content

    if has_tooldns:
        print("   📝 AGENTS.md already has ToolDNS instructions — replacing with updated version.")
    else:
        print("   📝 AGENTS.md needs ToolDNS instructions.")
    print("   This tells the agent the correct tool names and workflow.\n")

    choice = input("   Update AGENTS.md? [Y/n]: ").strip().lower()
    if choice not in ("", "y", "yes"):
        print("   ⏩ Skipped\n")
        return

    if has_tooldns:
        # Replace the existing ToolDNS section (from its heading to next ## heading or EOF)
        import re
        new_content = re.sub(
            r'## 🔍 ToolDNS.*?(?=\n## |\Z)',
            TOOLDNS_AGENT_INSTRUCTIONS,
            content,
            flags=re.DOTALL
        )
        # If regex didn't match (different heading format), fall back to append
        if new_content == content:
            new_content = content.rstrip() + "\n\n" + TOOLDNS_AGENT_INSTRUCTIONS + "\n"
    else:
        new_content = content.rstrip() + "\n\n" + TOOLDNS_AGENT_INSTRUCTIONS + "\n"

    agents_path.write_text(new_content, encoding="utf-8")
    print(f"   ✅ Updated {agents_path}\n")


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _sanitize_credentials(srv_name: str, srv_config: dict) -> dict:
    """
    Replace hardcoded API keys and user-specific URLs with ${ENV_VAR} references.
    Masks values when printing so raw credentials don't leak to stdout.
    """
    import re
    config = copy.deepcopy(srv_config)

    known_vars = {
        "composio": "COMPOSIO_API_KEY",
        "browser-use": "BROWSER_USE_API_KEY",
        "duckduckgo": "DUCKDUCKGO_API_KEY",
        "n8n-mcp": "N8N_MCP_INSTANCE_KEY",
    }

    # Sanitize URL — if it contains UUIDs or user_id, it's user-specific
    url = config.get("url", "")
    uuid_pattern = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
    if url and (uuid_pattern.search(url) or "user_id=" in url):
        env_var = f"{srv_name.upper().replace('-', '_')}_MCP_URL"
        config["url"] = f"${{{env_var}}}"
        masked_url = url[:30] + "***" if len(url) > 30 else url
        print(f"     → {srv_name}: URL contains user-specific data → ${{{env_var}}}")
        print(f"       Add to ~/.tooldns/.env: {env_var}={masked_url}")

    # Sanitize headers
    headers = config.get("headers", {})
    for header_name, header_value in headers.items():
        if isinstance(header_value, str) and not header_value.startswith("${"):
            env_var = known_vars.get(srv_name,
                f"{srv_name.upper().replace('-', '_')}_API_KEY")
            headers[header_name] = f"${{{env_var}}}"
            masked = header_value[:4] + "***" + header_value[-4:] if len(header_value) > 8 else "****"
            print(f"     → {srv_name}: credential → ${{{env_var}}}")
            print(f"       Add to ~/.tooldns/.env: {env_var}={masked}")

    # Check args for credential-like values
    args = config.get("args", [])
    for i, arg in enumerate(args):
        if isinstance(arg, str) and not arg.startswith("${"):
            for var_hint, env_var in known_vars.items():
                if var_hint in srv_name.lower() and ("key" in arg.lower() or "token" in arg.lower() or "bearer" in arg.lower()):
                    if ":" in arg:
                        prefix, value = arg.split(":", 1)
                        args[i] = f"{prefix}:${{{env_var}}}"
                        print(f"     → {srv_name}: arg credential → ${{{env_var}}}")

    return config


def _save_config(raw_config: dict, config_path: Path,
                 keys: list, mcp_section: dict):
    """Write updated MCP section back to the config file."""
    target = raw_config
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = mcp_section
    config_path.write_text(
        json.dumps(raw_config, indent=4, ensure_ascii=False),
        encoding="utf-8"
    )
