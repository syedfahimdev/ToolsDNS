"""cli.py — Interactive CLI for ToolsDNS.

Provides a command-line interface for setting up, managing,
and using ToolsDNS without running the HTTP server.

Features:
    - Install/update mechanism with ~/.tooldns home directory
    - Auto-detects known AI framework configs (cursor, claude-desktop, cline)
    - Interactive source management (add, list, remove)
    - Semantic tool search from the command line
    - Server management

Commands:
    tooldns install     — Create ~/.tooldns, install dependencies, run setup
    tooldns update      — Pull latest code and sync ~/.tooldns
    toolsdns setup       — Interactive first-time setup (auto-detects configs)
    toolsdns add         — Add a source interactively
    toolsdns sources     — List registered sources
    toolsdns tools       — List all indexed tools
    toolsdns search      — Search for a tool
    toolsdns ingest      — Re-ingest all sources
    toolsdns serve       — Start the API server

Usage:
    python3 -m tooldns.cli install
    python3 -m tooldns.cli add
    python3 -m tooldns.cli search "create a github issue"
    python3 -m tooldns.cli serve
"""

import sys
import json
import os
import secrets
import subprocess
from pathlib import Path
from tooldns.config import settings, logger, TOOLDNS_HOME
from tooldns.models import SourceType


# -------------------------------------------------------------------
# Known AI framework configs (auto-detection)
# -------------------------------------------------------------------

# Each entry: (display_name, config_path, config_key)
# config_key is the dot-separated JSON path to the mcpServers object.
KNOWN_CONFIGS = [
    {
        "name": "cursor",
        "path": "~/.cursor/mcp.json",
        "config_key": "mcpServers",
        "description": "Cursor IDE",
    },
    {
        "name": "claude-desktop",
        "path": "~/.config/claude/claude_desktop_config.json",
        "config_key": "mcpServers",
        "description": "Claude Desktop",
    },
    {
        "name": "cline",
        "path": "~/.cline/mcp_settings.json",
        "config_key": "mcpServers",
        "description": "Cline (VS Code extension)",
    },
]


def detect_configs() -> list[dict]:
    """
    Scan the filesystem for known AI framework config files.

    Checks each path in KNOWN_CONFIGS and returns the ones that exist.
    Also validates that the config_key path actually contains MCP servers.

    Returns:
        list[dict]: Detected configs with name, path, config_key, and
                    the number of MCP servers found.
    """
    detected = []
    for cfg in KNOWN_CONFIGS:
        full_path = Path(os.path.expanduser(cfg["path"]))
        if not full_path.exists():
            continue

        try:
            raw = json.loads(full_path.read_text(encoding="utf-8"))
            # Navigate to the mcpServers section
            section = raw
            for key in cfg["config_key"].split("."):
                section = section.get(key, {})

            if section and isinstance(section, dict):
                detected.append({
                    **cfg,
                    "full_path": str(full_path),
                    "server_count": len(section),
                    "server_names": list(section.keys()),
                })
        except Exception:
            continue

    return detected


# -------------------------------------------------------------------
# Component initialization
# -------------------------------------------------------------------

def get_components():
    """
    Initialize and return all ToolsDNS components.

    Creates the database, embedder, search engine, and ingestion
    pipeline. Used by CLI commands that need the full stack.

    Returns:
        tuple: (database, embedder, search_engine, ingestion_pipeline)
    """
    from tooldns.database import ToolDatabase
    from tooldns.embedder import Embedder
    from tooldns.search import SearchEngine
    from tooldns.ingestion import IngestionPipeline

    db = ToolDatabase(settings.db_path)
    embedder = Embedder()
    search = SearchEngine(db, embedder)
    pipeline = IngestionPipeline(db, embedder)
    return db, embedder, search, pipeline


def print_banner():
    """Print the ToolsDNS ASCII banner."""
    print("""
╔════════════════════════════════════════╗
║            ⚡ ToolsDNS ⚡               ║
║     DNS for AI Tools — v1.0.0         ║
║                                        ║
║  Search 10,000 tools. Return only 1.  ║
╚════════════════════════════════════════╝
    """)


# -------------------------------------------------------------------
# Commands
# -------------------------------------------------------------------

def cmd_install():
    """
    Install ToolsDNS: create ~/.tooldns home directory, install deps, run setup.

    This is the first command a user runs after cloning the repo.
    It creates the persistent home directory, installs Python
    dependencies, records the repo path for updates, and then
    runs the interactive setup wizard.
    """
    print_banner()
    home = TOOLDNS_HOME
    repo_dir = Path(__file__).parent.parent.resolve()

    print(f"📦 Installing ToolsDNS...\n")
    print(f"   Home directory: {home}")
    print(f"   Repo directory: {repo_dir}")

    # Create home directory and subdirectories
    home.mkdir(parents=True, exist_ok=True)
    (home / "skills").mkdir(exist_ok=True)
    (home / "tools").mkdir(exist_ok=True)
    print(f"   ✅ Created {home}")
    print(f"   ✅ Created {home}/skills/ (drop skill folders here)")
    print(f"   ✅ Created {home}/tools/  (drop .py tool files here)")

    # Create/update config.json with auto-detected skill paths
    config_file = home / "config.json"
    existing_config = {}
    if config_file.exists():
        try:
            existing_config = json.loads(config_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Auto-detect skill directories
    skill_dirs = []
    known_skill_paths = [
        Path.home() / ".agents" / "skills",
        Path.home() / ".tooldns" / "skills",
    ]
    for sp in known_skill_paths:
        if sp.exists() and any(sp.iterdir()):
            skill_dirs.append(str(sp))

    if not config_file.exists():
        # Create fresh config with examples and detected paths
        config_data = {
            "mcpServers": {},
            "skillPaths": skill_dirs,
            "_examples": {
                "streamableHttp": {
                    "type": "streamableHttp",
                    "url": "https://your-server.com/mcp",
                    "headers": {"Authorization": "Bearer ${YOUR_API_KEY}"}
                },
                "sse": {
                    "type": "sse",
                    "url": "https://your-sse-server.com/mcp",
                    "headers": {"x-api-key": "${YOUR_API_KEY}"}
                },
                "stdio_npx": {
                    "command": "npx",
                    "args": ["-y", "your-mcp-package"]
                }
            }
        }
        config_file.write_text(json.dumps(config_data, indent=2))
        print(f"   ✅ Created config.json (supports streamableHttp, sse, stdio/npx)")
    else:
        # Update existing config with newly detected skill paths
        current_paths = set(existing_config.get("skillPaths", []))
        new_paths = [p for p in skill_dirs if p not in current_paths]
        if new_paths:
            existing_config.setdefault("skillPaths", []).extend(new_paths)
            config_file.write_text(json.dumps(existing_config, indent=2))
            print(f"   ✅ Added {len(new_paths)} skill path(s) to config.json")

    if skill_dirs:
        for sd in skill_dirs:
            skill_count = sum(1 for d in Path(sd).iterdir()
                            if d.is_dir() and (d / "SKILL.md").exists())
            print(f"   📁 Found skills: {sd} ({skill_count} skills)")

    # Save repo path so 'update' knows where to git pull
    repo_file = home / "repo_path"
    repo_file.write_text(str(repo_dir))
    print(f"   ✅ Repo path saved")

    # Install dependencies
    print(f"\n⏳ Installing Python dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--break-system-packages", "-q", "-r",
         str(repo_dir / "requirements.txt")],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("   ✅ Dependencies installed")
    else:
        # Try without --break-system-packages
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r",
             str(repo_dir / "requirements.txt")],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print("   ✅ Dependencies installed")
        else:
            print(f"   ⚠ Dependency install issue: {result.stderr[:200]}")

    # Install tooldns as a package so 'python3 -m tooldns.mcp_server' works globally
    print("⏳ Installing ToolsDNS package...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--break-system-packages", "-q", "-e", str(repo_dir)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        # Try without --break-system-packages
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-e", str(repo_dir)],
            capture_output=True, text=True
        )
    if result.returncode == 0:
        print("   ✅ tooldns package installed (python3 -m tooldns.mcp_server)")
    else:
        print(f"   ⚠ Package install issue: {result.stderr[:200]}")

    # Run setup
    print()
    cmd_setup()


def cmd_update():
    """
    Update ToolsDNS: pull latest code, reinstall, and restart services.

    Reads the saved repo path from ~/.tooldns/repo_path, runs git pull,
    reinstalls the package, and restarts tooldns + tooldns-mcp services.
    """
    import time
    import urllib.request
    print_banner()
    home = TOOLDNS_HOME
    repo_file = home / "repo_path"

    if not repo_file.exists():
        print("❌ ToolsDNS not installed. Run 'toolsdns install' first.")
        return

    repo_dir = Path(repo_file.read_text().strip())
    if not repo_dir.exists():
        print(f"❌ Repo not found at {repo_dir}")
        print(f"   Update the path in {repo_file}")
        return

    print(f"🔄 Updating ToolsDNS from {repo_dir}\n")

    # 1. Git pull
    print("⏳ Pulling latest code...")
    result = subprocess.run(["git", "pull"], cwd=str(repo_dir), capture_output=True, text=True)
    if result.returncode == 0:
        msg = result.stdout.strip()
        print(f"   ✅ {msg}")
        if "Already up to date" in msg:
            print("   (no code changes — reinstalling anyway to apply any local edits)")
    else:
        print(f"   ❌ Git pull failed:\n{result.stderr[:300]}")
        return

    # 2. Reinstall package
    print("\n⏳ Reinstalling package...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(repo_dir),
         "--break-system-packages", "-q"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        # Fallback without --break-system-packages (venv)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(repo_dir), "-q"],
            capture_output=True, text=True
        )
    print("   ✅ Package installed")

    # 3. Restart services
    print("\n⏳ Restarting services...")
    for svc in ["tooldns", "tooldns-mcp"]:
        r = subprocess.run(["systemctl", "restart", svc], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"   ✅ {svc} restarted")
        else:
            print(f"   ⚠  {svc}: {r.stderr.strip()[:80] or 'not found (systemd)'}")

    # 4. Wait for health
    print("\n⏳ Waiting for server to come up...")
    api_port = settings.port
    for i in range(20):
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"http://localhost:{api_port}/health", timeout=2) as resp:
                import json as _json
                data = _json.loads(resp.read())
                tools = data.get("tools_indexed", "?")
                sources = data.get("sources", "?")
                print(f"\n✅ ToolsDNS is up — {tools} tools from {sources} sources")
                print(f"   Health: http://localhost:{api_port}/health")
                return
        except Exception:
            print(f"   waiting... ({(i+1)*2}s)", end="\r")

    print("\n⚠  Server didn't respond in 40s — check: journalctl -u tooldns -n 20")


def cmd_setup():
    """
    Interactive first-time setup wizard with auto-detection.

    Walks the user through:
    1. Generating an API key
    2. Setting the server port
    3. Auto-detecting known AI framework configs
    4. Ingesting detected sources
    5. Writing the .env file to ~/.tooldns/
    """
    home = TOOLDNS_HOME
    home.mkdir(parents=True, exist_ok=True)

    env_path = home / ".env"
    env_vars = {}

    # Check if already configured
    if env_path.exists():
        print("   ℹ️  Existing config found at", env_path)
        reuse = input("   Keep existing config? [Y/n]: ").strip().lower()
        if reuse != "n":
            print("   ✅ Keeping existing config")
            _run_auto_detect()
            return

    # API Key
    print("1️⃣  API Key")
    print("   This key protects your ToolsDNS API.")
    gen = input("   Generate a random key? [Y/n]: ").strip().lower()
    if gen != "n":
        key = "td_" + secrets.token_hex(24)
        print(f"   ✅ Generated: {key}")
    else:
        key = input("   Enter your API key: ").strip()
    env_vars["TOOLDNS_API_KEY"] = key

    # Port
    print("\n2️⃣  Server Port")
    port = input(f"   Port [{settings.port}]: ").strip()
    if port:
        env_vars["TOOLDNS_PORT"] = port

    # Webhook (optional)
    print("\n3️⃣  Webhook URL (optional)")
    print("   ToolsDNS will POST here when a source goes down or recovers.")
    print("   Works with Slack, Discord, PagerDuty, or any HTTP endpoint.")
    print("   Examples:")
    print("     Slack:   https://hooks.slack.com/services/T.../B.../...")
    print("     Discord: https://discord.com/api/webhooks/...")
    print("     Custom:  https://yourserver.com/toolsdns-alert")
    webhook = input("   Webhook URL (leave blank to skip): ").strip()
    if webhook:
        env_vars["TOOLDNS_WEBHOOK_URL"] = webhook
        secret = input("   Webhook secret (optional, sent as X-ToolsDNS-Secret header): ").strip()
        if secret:
            env_vars["TOOLDNS_WEBHOOK_SECRET"] = secret

    # Write .env to home directory
    with open(env_path, "w") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")
    print(f"\n✅ Config saved to {env_path}")

    # Auto-detect
    _run_auto_detect()

    print("\n🎉 Setup complete! Start the server with:")
    print("   python3 -m tooldns.cli serve")
    print("   # or via Docker:")
    print("   docker compose up -d")


def _run_auto_detect():
    """
    Auto-detect AI framework configs and offer selective ingestion.

    Scans for known configs (cursor, claude-desktop, cline), shows what was
    found, and lets the user pick which configs AND which servers
    within each config to ingest.
    """
    print("\n🔍 Auto-detecting AI framework configs...")
    detected = detect_configs()

    if not detected:
        print("   No known configs found.")
        add = input("\n   Add a source manually? [Y/n]: ").strip().lower()
        if add != "n":
            cmd_add()
        return

    print(f"\n   Found {len(detected)} config(s):\n")
    for i, cfg in enumerate(detected, 1):
        servers = ", ".join(cfg["server_names"])
        print(f"   {i}) {cfg['name']} — {cfg['description']}")
        print(f"      MCP servers ({cfg['server_count']}): {servers}")
        print()

    print("   Options:")
    print("   • Enter numbers to select (e.g., '1' or '1,2')")
    print("   • 'all' to ingest everything")
    print("   • 'skip' to skip")
    choice = input("\n   Select configs to ingest: ").strip().lower()

    if choice == "skip":
        print("   Skipped. Run 'toolsdns add' later.")
        return

    if choice == "all":
        selected = detected
    else:
        indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip().isdigit()]
        selected = [detected[i] for i in indices if 0 <= i < len(detected)]

    if not selected:
        print("   No valid selection.")
        return

    db_comp, embedder, search, pipeline = get_components()

    for cfg in selected:
        print(f"\n   📦 {cfg['name']} has {cfg['server_count']} MCP server(s):")
        for j, srv in enumerate(cfg["server_names"], 1):
            print(f"      {j}) {srv}")

        srv_choice = input(f"   Which servers? [all / 1,2,... / skip]: ").strip().lower()

        if srv_choice == "skip":
            print(f"   Skipped {cfg['name']}.")
            continue

        if srv_choice == "all" or srv_choice == "":
            skip_servers = set()
        else:
            selected_indices = {int(x.strip()) - 1 for x in srv_choice.split(",") if x.strip().isdigit()}
            # skip_servers = servers NOT selected
            skip_servers = {
                cfg["server_names"][j]
                for j in range(len(cfg["server_names"]))
                if j not in selected_indices
            }

        print(f"\n   ⏳ Ingesting '{cfg['name']}'...")
        try:
            config = {
                "type": SourceType.MCP_CONFIG,
                "name": cfg["name"],
                "path": cfg["full_path"],
                "config_key": cfg["config_key"],
                "skip_servers": list(skip_servers),
            }
            count = pipeline.ingest_source(config)
            print(f"   ✅ {cfg['name']}: indexed {count} tools")
        except Exception as e:
            print(f"   ❌ {cfg['name']}: {e}")


def cmd_add():
    """
    Interactive source addition wizard.

    Presents a menu of source types. If MCP configs are detected
    on the system, offers them as quick-add options first.
    """
    db, embedder, search, pipeline = get_components()

    # Check for unregistered configs
    detected = detect_configs()
    existing_sources = {s["name"] for s in db.get_all_sources()}
    new_detected = [c for c in detected if c["name"] not in existing_sources]

    if new_detected:
        print("\n🔍 Detected configs not yet registered:\n")
        for i, cfg in enumerate(new_detected, 1):
            servers = ", ".join(cfg["server_names"])
            print(f"   {i}) {cfg['name']} — {servers}")
        print(f"   {len(new_detected) + 1}) Add a different source manually")
        print()

        choice = input(f"   Choice [1-{len(new_detected) + 1}]: ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(new_detected):
                cfg = new_detected[idx]
                config = {
                    "type": SourceType.MCP_CONFIG,
                    "name": cfg["name"],
                    "path": cfg["full_path"],
                    "config_key": cfg["config_key"],
                }
                print(f"\n⏳ Ingesting tools from '{cfg['name']}'...")
                count = pipeline.ingest_source(config)
                print(f"✅ Success! Indexed {count} tools from '{cfg['name']}'.")
                return
        except (ValueError, IndexError):
            pass

    # Manual add menu
    print("\n📦 Add a Tool Source")
    print("   What type of source?\n")
    print("   1) MCP Config File    — Read all MCP servers from a config.json")
    print("   2) MCP Server (stdio) — Connect to a local MCP server process")
    print("   3) MCP Server (HTTP)  — Connect to a remote MCP server URL")
    print("   4) Skill Directory    — Read skill .md files from a folder")
    print("   5) Custom Tool        — Register a single tool manually")
    print()

    choice = input("   Choice [1-5]: ").strip()

    config = {}

    if choice == "1":
        config["type"] = SourceType.MCP_CONFIG
        config["name"] = input("   Source name (e.g., 'my-tools'): ").strip()
        config["path"] = input("   Config file path: ").strip()
        if not config["path"]:
            print("   ❌ Path is required.")
            return
        config["config_key"] = input("   JSON path to MCP servers [tools.mcpServers]: ").strip() or "tools.mcpServers"

    elif choice == "2":
        config["type"] = SourceType.MCP_STDIO
        config["name"] = input("   Source name (e.g., 'my-skills'): ").strip()
        config["command"] = input("   Command (e.g., python3): ").strip()
        if not config["command"]:
            print("   ❌ Command is required.")
            return
        args_str = input("   Arguments (space-separated): ").strip()
        config["args"] = args_str.split() if args_str else []

    elif choice == "3":
        config["type"] = SourceType.MCP_HTTP
        config["name"] = input("   Source name (e.g., 'composio'): ").strip()
        config["url"] = input("   Server URL: ").strip()
        if not config["url"]:
            print("   ❌ URL is required.")
            return
        has_headers = input("   Custom headers? [y/N]: ").strip().lower()
        if has_headers == "y":
            headers = {}
            while True:
                key = input("   Header key (empty to stop): ").strip()
                if not key:
                    break
                val = input(f"   {key} value: ").strip()
                headers[key] = val
            config["headers"] = headers

    elif choice == "4":
        config["type"] = SourceType.SKILL_DIRECTORY
        config["name"] = input("   Source name (e.g., 'my-skills'): ").strip()
        config["path"] = input("   Directory path: ").strip()
        if not config["path"]:
            print("   ❌ Path is required.")
            return

    elif choice == "5":
        config["type"] = SourceType.CUSTOM
        config["name"] = input("   Source name: ").strip()
        config["tool_name"] = input("   Tool name: ").strip()
        config["tool_description"] = input("   Tool description: ").strip()
        schema_str = input("   Input schema JSON (or empty for {}): ").strip()
        config["tool_schema"] = json.loads(schema_str) if schema_str else {}

    else:
        print("   ❌ Invalid choice.")
        return

    if not config.get("name"):
        print("   ❌ Source name is required.")
        return

    # Ingest
    print(f"\n⏳ Ingesting tools from '{config['name']}'...")
    try:
        count = pipeline.ingest_source(config)
        print(f"✅ Success! Indexed {count} tools from '{config['name']}'.")
    except Exception as e:
        print(f"❌ Error: {e}")


def cmd_sources():
    """List all registered sources."""
    db, _, _, _ = get_components()
    sources = db.get_all_sources()

    if not sources:
        print("No sources registered. Run 'toolsdns add' to add one.")
        return

    print(f"\n📋 Registered Sources ({len(sources)}):\n")
    for s in sources:
        status_icon = "✅" if s["status"] == "active" else "❌"
        print(f"  {status_icon} {s['name']}")
        print(f"     Type: {s['type']} | Tools: {s['tools_count']} | ID: {s['id']}")
        if s["error"]:
            print(f"     Error: {s['error']}")
        print()


def cmd_tools(source_filter: str = None):
    """
    List all indexed tools.

    Args:
        source_filter: Optional source name to filter by.
    """
    db, _, _, _ = get_components()

    if source_filter:
        tools = db.get_tools_by_source(source_filter)
    else:
        tools = db.get_all_tools()

    if not tools:
        print("No tools indexed. Run 'toolsdns add' to add a source.")
        return

    print(f"\n🔧 Indexed Tools ({len(tools)}):\n")
    for t in tools:
        source = t.get("source_info", {}).get("source_name", "?")
        print(f"  • {t['name']}")
        print(f"    {t['description'][:80]}")
        print(f"    Source: {source}")
        print()


def cmd_search(query: str):
    """
    Search for tools matching a query.

    Args:
        query: Natural language query describing what tool is needed.
    """
    _, _, search, _ = get_components()

    print(f"\n🔍 Searching for: \"{query}\"\n")
    response = search.search(query, top_k=5)

    if not response.results:
        print("  No matching tools found.")
        return

    print(f"  Found {len(response.results)} result(s) "
          f"({response.search_time_ms:.1f}ms, "
          f"~{response.tokens_saved} tokens saved):\n")

    for i, r in enumerate(response.results, 1):
        bar = "█" * int(r.confidence * 20)
        print(f"  {i}. {r.name} ({r.confidence:.1%})")
        print(f"     {r.description[:80]}")
        print(f"     Source: {r.source} | Confidence: [{bar:<20}]")
        print()


def cmd_status():
    """
    Show ToolsDNS system status: config, sources, tools, health.

    Displays a comprehensive overview of the current state including
    home directory, database stats, source health, and sample tools.
    """
    from tooldns.config import TOOLDNS_HOME
    home = TOOLDNS_HOME

    print_banner()
    print("📊 ToolsDNS Status\n")

    # Home directory
    print(f"   Home:     {home}")
    print(f"   Config:   {home / '.env'} {'✅' if (home / '.env').exists() else '❌ missing'}")
    print(f"   Database: {settings.db_path}")
    print(f"   Log:      {home / 'tooldns.log'}")

    repo_file = home / "repo_path"
    if repo_file.exists():
        print(f"   Repo:     {repo_file.read_text().strip()}")
    print()

    # Database stats
    db, _, _, _ = get_components()
    tool_count = db.get_tool_count()
    sources = db.get_all_sources()

    print(f"   📦 Sources: {len(sources)}")
    print(f"   🔧 Tools indexed: {tool_count}")
    print()

    # Source details
    if sources:
        print("   Sources:")
        for s in sources:
            icon = "✅" if s["status"] == "active" else "❌"
            print(f"     {icon} {s['name']} — {s['tools_count']} tools ({s['type']})")
            if s.get("error"):
                print(f"        Error: {s['error']}")
        print()

    # Sample tools
    if tool_count > 0:
        tools = db.get_all_tools()
        print(f"   Sample tools (showing first 5 of {tool_count}):")
        for t in tools[:5]:
            src = t.get("source_info", {}).get("source_name", "?")
            print(f"     • {t['name']} (from {src})")
        if tool_count > 5:
            print(f"     ... and {tool_count - 5} more")
        print()

    # Log file check
    log_path = home / "tooldns.log"
    if log_path.exists():
        lines = log_path.read_text().strip().split("\n")
        print(f"   📝 Log: {len(lines)} lines (last 3):")
        for line in lines[-3:]:
            print(f"      {line}")
    print()

    # Refresh interval
    print(f"   🔄 Auto-refresh: every {settings.refresh_interval} min")
    print(f"   🌐 Server: http://{settings.host}:{settings.port}")
    print(f"   📖 API docs: http://localhost:{settings.port}/docs")
    print()
    print("   All good! ✅" if tool_count > 0 else "   ⚠️  No tools indexed. Run 'toolsdns add'.")


def cmd_install_mcp():
    """
    Interactive wizard to install a new MCP server into ToolsDNS.

    Guides the user through:
    1. Picking the package type (npx, pip, or custom command)
    2. Installing the package
    3. Setting required environment variables
    4. Adding the server to ~/.tooldns/config.json
    5. Ingesting its tools
    """
    import subprocess as sp

    print("\n📦 Install a New MCP Server\n")
    print("   This will install the package, save credentials, and index its tools.\n")

    # Step 1: Package type
    print("   What kind of MCP server?")
    print("   1) npm / npx  (e.g., @modelcontextprotocol/server-github)")
    print("   2) pip / Python  (e.g., toolsdns)")
    print("   3) Custom command  (already installed, just configure it)")
    print()
    pkg_type = input("   Choice [1-3]: ").strip()

    command = ""
    args = []
    server_name = ""
    transport = "stdio"
    url = ""

    if pkg_type == "1":
        pkg = input("   npm package name (e.g., @modelcontextprotocol/server-github): ").strip()
        if not pkg:
            print("   ❌ Package name required.")
            return
        server_name = input(f"   Short name for this server [{pkg.split('/')[-1]}]: ").strip() or pkg.split("/")[-1]
        print(f"\n   ⏳ Installing {pkg}...")
        result = sp.run(["npm", "install", "-g", pkg], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"   ✅ Installed {pkg}")
        else:
            print(f"   ⚠ npm install failed: {result.stderr[:200]}")
            print("   Continuing with configuration anyway...")
        command = "npx"
        args = ["-y", pkg]

    elif pkg_type == "2":
        pkg = input("   pip package name (e.g., mcp-server-fetch): ").strip()
        if not pkg:
            print("   ❌ Package name required.")
            return
        server_name = input(f"   Short name for this server [{pkg.replace('-', '_')}]: ").strip() or pkg.replace("-", "_")
        print(f"\n   ⏳ Installing {pkg}...")
        result = sp.run([sys.executable, "-m", "pip", "install", "--break-system-packages", "-q", pkg],
                       capture_output=True, text=True)
        if result.returncode != 0:
            result = sp.run([sys.executable, "-m", "pip", "install", "-q", pkg],
                           capture_output=True, text=True)
        if result.returncode == 0:
            print(f"   ✅ Installed {pkg}")
        else:
            print(f"   ⚠ pip install failed: {result.stderr[:200]}")
            print("   Continuing with configuration anyway...")
        command = sys.executable
        args_str = input(f"   Python module or script to run (e.g., -m mcp_server_fetch): ").strip()
        args = args_str.split() if args_str else ["-m", pkg.replace("-", "_")]

    elif pkg_type == "3":
        server_name = input("   Short name for this server: ").strip()
        if not server_name:
            print("   ❌ Server name required.")
            return
        print("   Transport type?")
        print("   1) stdio (local subprocess)")
        print("   2) HTTP (remote URL)")
        t = input("   Choice [1/2]: ").strip()
        if t == "2":
            transport = "http"
            url = input("   Server URL: ").strip()
        else:
            command = input("   Command (e.g., python3, node, npx): ").strip()
            args_str = input("   Arguments (space-separated): ").strip()
            args = args_str.split() if args_str else []
    else:
        print("   ❌ Invalid choice.")
        return

    if not server_name:
        print("   ❌ Server name required.")
        return

    # Step 2: Environment variables
    print(f"\n   🔑 Environment Variables for '{server_name}'")
    print("   Enter any API keys or tokens this server needs.")
    print("   They'll be saved to ~/.tooldns/.env and referenced as ${VAR_NAME}.\n")

    env_vars = {}
    while True:
        var_name = input("   Variable name (empty to skip/finish): ").strip().upper()
        if not var_name:
            break
        var_value = input(f"   {var_name}=: ").strip()
        if var_value:
            env_vars[var_name] = var_value

    # Save env vars
    if env_vars:
        env_path = TOOLDNS_HOME / ".env"
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()
        existing.update(env_vars)
        env_path.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n")
        print(f"   ✅ Saved {len(env_vars)} variable(s) to {env_path}")

        # Also export them for the current process so ingestion works immediately
        import os
        for k, v in env_vars.items():
            os.environ[k] = v

    # Step 3: Add to config.json
    config_file = TOOLDNS_HOME / "config.json"
    config_data = {}
    if config_file.exists():
        try:
            config_data = json.loads(config_file.read_text())
        except Exception:
            pass

    mcp_servers = config_data.setdefault("mcpServers", {})

    if transport == "http":
        mcp_servers[server_name] = {"type": "streamableHttp", "url": url}
    else:
        # Replace actual env var values with ${VAR} references in args
        safe_args = []
        for arg in args:
            for var_name, var_value in env_vars.items():
                if var_value and var_value in arg:
                    arg = arg.replace(var_value, f"${{{var_name}}}")
            safe_args.append(arg)
        mcp_servers[server_name] = {"command": command, "args": safe_args}

    config_file.write_text(json.dumps(config_data, indent=2))
    print(f"   ✅ Added '{server_name}' to {config_file}")

    # Step 4: Ingest
    print(f"\n   ⏳ Indexing tools from '{server_name}'...")
    try:
        _, _, _, pipeline = get_components()
        source_config = {
            "type": "mcp_config",
            "name": f"local-{server_name}",
            "path": str(config_file),
            "config_key": "mcpServers",
        }
        count = pipeline.ingest_source(source_config)
        print(f"   ✅ Indexed {count} tools from '{server_name}'")
    except Exception as e:
        print(f"   ⚠ Ingestion failed: {e}")
        print("   Run './tooldns.sh ingest' after verifying the server works.")

    print(f"\n🎉 '{server_name}' is ready! Your agent can now search and use its tools via ToolsDNS.")


def cmd_new_skill():
    """
    Create a new skill file in the ToolsDNS skills directory.

    Skills are markdown files that teach the LLM how to call an API
    or perform a multi-step task. They live in ~/.tooldns/skills/ or
    any path listed under skillPaths in config.json.
    """
    print("\n✏️  Create a New Skill\n")

    # Determine where to save
    config_file = TOOLDNS_HOME / "config.json"
    skill_dirs = [TOOLDNS_HOME / "skills"]

    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text())
            for sp_str in cfg.get("skillPaths", []):
                p = Path(os.path.expanduser(sp_str))
                if p.exists() and p not in skill_dirs:
                    skill_dirs.append(p)
        except Exception:
            pass

    if len(skill_dirs) > 1:
        print("   Where should this skill be saved?")
        for i, d in enumerate(skill_dirs, 1):
            print(f"   {i}) {d}")
        choice = input(f"   Choice [1-{len(skill_dirs)}]: ").strip()
        try:
            skill_dir = skill_dirs[int(choice) - 1]
        except (ValueError, IndexError):
            skill_dir = skill_dirs[0]
    else:
        skill_dir = skill_dirs[0]

    skill_dir.mkdir(parents=True, exist_ok=True)

    # Skill details
    name = input("\n   Skill name (e.g., github, send-report): ").strip()
    if not name:
        print("   ❌ Name required.")
        return
    description = input("   One-line description: ").strip() or f"Skill: {name}"

    # Write template
    skill_folder = skill_dir / name
    skill_folder.mkdir(exist_ok=True)
    skill_file = skill_folder / "SKILL.md"

    template = f"""---
name: {name}
description: {description}
---

# {name.title()}

{description}

## How to use

WHEN: Describe when the agent should use this skill
TEMPLATE:
  EXTRACT:
    param1: Description of param1
    param2: Description of param2
  EXAMPLE:
    param1: example value
    param2: example value

## Instructions

Write step-by-step instructions here for what the LLM should do.
Include any API endpoints, request formats, or external calls needed.
"""

    skill_file.write_text(template)
    print(f"\n   ✅ Created: {skill_file}")
    print(f"\n   Edit the file to add your skill instructions:")
    print(f"   nano {skill_file}")
    print(f"\n   Then run './tooldns.sh ingest' to index it.")


def cmd_system_prompt():
    """
    Generate a ready-to-paste system prompt for your AI agent.
    Fetches live tool count + skill list from the running server.
    """
    import urllib.request
    import urllib.error

    api_key = settings.api_key
    base_url = f"http://127.0.0.1:{settings.port}"

    try:
        req = urllib.request.Request(
            f"{base_url}/v1/system-prompt",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            prompt = r.read().decode()
        print("\n" + "=" * 70)
        print("  TOOLDNS SYSTEM PROMPT — paste this into your agent's system prompt")
        print("=" * 70 + "\n")
        print(prompt)
        print("=" * 70)
        print("\nTip: copy everything between the === lines and paste into your agent.")
    except urllib.error.URLError:
        print("❌  Could not connect to ToolsDNS. Is the server running?")
        print(f"   Start it with: tooldns serve   (expected on {base_url})")


def cmd_ingest():
    """Re-ingest all registered sources."""
    _, _, _, pipeline = get_components()

    print("⏳ Re-ingesting all sources...")
    total = pipeline.ingest_all()
    print(f"✅ Done! Total tools indexed: {total}")


def cmd_serve():
    """Start the ToolsDNS API server."""
    import uvicorn
    print_banner()

    # Resolve the repo directory for correct main.py import
    repo_dir = Path(__file__).parent.parent.resolve()

    print(f"🚀 Starting ToolsDNS server on {settings.host}:{settings.port}")
    print(f"   Home: {settings.home}")
    print(f"   Repo: {repo_dir}")
    print(f"   API docs: http://localhost:{settings.port}/docs\n")

    # Ensure the repo dir is in the Python path so main.py can be found
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False
    )


def main():
    """
    CLI entry point. Dispatches to the appropriate command handler.

    Usage:
        python3 -m tooldns.cli <command> [args]

    Commands:
        install    Create ~/.tooldns, install deps, run setup
        update     Pull latest code and sync dependencies
        setup      Interactive config + auto-detect sources
        integrate  Wire ToolsDNS into supported agent frameworks
        add        Add a tool source interactively
        sources    List registered sources
        tools      List indexed tools
        search     Search for a tool by query
        status     Show system status and health
        ingest     Re-ingest all sources
        serve      Start the API server
    """
    if len(sys.argv) < 2:
        print_banner()
        print("Usage: python3 -m tooldns.cli <command>\n")
        print("Commands:")
        print("  install      Create ~/.tooldns, install deps, run setup")
        print("  update       Pull latest code and sync dependencies")
        print("  setup        Interactive config + auto-detect sources")
        print("  integrate    Wire ToolsDNS into supported agent frameworks")
        print("  install-mcp  Install a new MCP server + set env vars")
        print("  new-skill    Create a new skill file template")
        print("  add          Add a tool source interactively")
        print("  sources      List registered sources")
        print("  tools        List indexed tools [--source NAME]")
        print("  search       Search for a tool")
        print("  status       Show system status and health")
        print("  ingest       Re-ingest all sources")
        print("  system-prompt  Generate system prompt to paste into your agent")
        print("  serve        Start the API server")
        return

    cmd = sys.argv[1]

    if cmd == "install":
        cmd_install()
    elif cmd == "update":
        cmd_update()
    elif cmd == "setup":
        cmd_setup()
    elif cmd == "integrate":
        from tooldns.integrate import run_integrate
        print_banner()
        run_integrate()
    elif cmd == "install-mcp":
        cmd_install_mcp()
    elif cmd == "new-skill":
        cmd_new_skill()
    elif cmd == "add":
        cmd_add()
    elif cmd == "sources":
        cmd_sources()
    elif cmd == "tools":
        source = None
        if "--source" in sys.argv:
            idx = sys.argv.index("--source")
            if idx + 1 < len(sys.argv):
                source = sys.argv[idx + 1]
        cmd_tools(source)
    elif cmd == "search":
        if len(sys.argv) < 3:
            query = input("Enter search query: ").strip()
        else:
            query = " ".join(sys.argv[2:])
        cmd_search(query)
    elif cmd == "status":
        cmd_status()
    elif cmd == "ingest":
        cmd_ingest()
    elif cmd == "serve":
        cmd_serve()
    elif cmd == "system-prompt":
        cmd_system_prompt()
    else:
        print(f"Unknown command: {cmd}")
        print("Run 'python3 -m tooldns.cli' for help.")


if __name__ == "__main__":
    main()
