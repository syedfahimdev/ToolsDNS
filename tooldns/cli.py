"""cli.py — Interactive CLI for ToolDNS.

Provides a command-line interface for setting up, managing,
and using ToolDNS without running the HTTP server.

Features:
    - Install/update mechanism with ~/.tooldns home directory
    - Auto-detects known AI framework configs (nanobot, openclaw)
    - Interactive source management (add, list, remove)
    - Semantic tool search from the command line
    - Server management

Commands:
    tooldns install     — Create ~/.tooldns, install dependencies, run setup
    tooldns update      — Pull latest code and sync ~/.tooldns
    tooldns setup       — Interactive first-time setup (auto-detects configs)
    tooldns add         — Add a source interactively
    tooldns sources     — List registered sources
    tooldns tools       — List all indexed tools
    tooldns search      — Search for a tool
    tooldns ingest      — Re-ingest all sources
    tooldns serve       — Start the API server

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
        "name": "nanobot",
        "path": "~/.nanobot/config.json",
        "config_key": "tools.mcpServers",
        "description": "Nanobot AI agent framework",
    },
    {
        "name": "openclaw",
        "path": "~/.openclaw/workspace/config/mcporter.json",
        "config_key": "mcpServers",
        "description": "OpenClaw agent framework (mcporter)",
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
    Initialize and return all ToolDNS components.

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
    """Print the ToolDNS ASCII banner."""
    print("""
╔════════════════════════════════════════╗
║            ⚡ ToolDNS ⚡               ║
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
    Install ToolDNS: create ~/.tooldns home directory, install deps, run setup.

    This is the first command a user runs after cloning the repo.
    It creates the persistent home directory, installs Python
    dependencies, records the repo path for updates, and then
    runs the interactive setup wizard.
    """
    print_banner()
    home = TOOLDNS_HOME
    repo_dir = Path(__file__).parent.parent.resolve()

    print(f"📦 Installing ToolDNS...\n")
    print(f"   Home directory: {home}")
    print(f"   Repo directory: {repo_dir}")

    # Create home directory and subdirectories
    home.mkdir(parents=True, exist_ok=True)
    (home / "skills").mkdir(exist_ok=True)
    (home / "tools").mkdir(exist_ok=True)
    print(f"   ✅ Created {home}")
    print(f"   ✅ Created {home}/skills/ (drop skill folders here)")
    print(f"   ✅ Created {home}/tools/  (drop .py tool files here)")

    # Create example config.json if it doesn't exist
    config_file = home / "config.json"
    if not config_file.exists():
        config_file.write_text(json.dumps({
            "mcpServers": {
                "_example": {
                    "type": "streamableHttp",
                    "url": "https://your-mcp-server.com/mcp",
                    "headers": {"Authorization": "Bearer ${YOUR_API_KEY}"},
                    "_note": "Remove this entry and add your own MCP servers. Use ${ENV_VAR} for credentials."
                }
            }
        }, indent=2))
        print(f"   ✅ Created example config.json (add your MCP servers)")

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

    # Run setup
    print()
    cmd_setup()


def cmd_update():
    """
    Update ToolDNS: pull latest code from git and reinstall dependencies.

    Reads the saved repo path from ~/.tooldns/repo_path, runs git pull,
    and reinstalls dependencies to pick up any changes.
    """
    print_banner()
    home = TOOLDNS_HOME
    repo_file = home / "repo_path"

    if not repo_file.exists():
        print("❌ ToolDNS not installed. Run 'python3 -m tooldns.cli install' first.")
        return

    repo_dir = Path(repo_file.read_text().strip())
    if not repo_dir.exists():
        print(f"❌ Repo not found at {repo_dir}")
        print(f"   Update the path in {repo_file}")
        return

    print(f"🔄 Updating ToolDNS...")
    print(f"   Repo: {repo_dir}\n")

    # Git pull
    print("⏳ Pulling latest code...")
    result = subprocess.run(
        ["git", "pull"],
        cwd=str(repo_dir),
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"   ✅ {result.stdout.strip()}")
    else:
        print(f"   ❌ Git pull failed: {result.stderr[:200]}")
        return

    # Reinstall dependencies
    print("\n⏳ Updating dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--break-system-packages", "-q", "-r",
         str(repo_dir / "requirements.txt")],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("   ✅ Dependencies up to date")
    else:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r",
             str(repo_dir / "requirements.txt")],
            capture_output=True, text=True
        )
        print("   ✅ Dependencies up to date")

    print("\n🎉 ToolDNS updated! Restart the server to apply changes.")


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
    print("   This key protects your ToolDNS API.")
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

    # Write .env to home directory
    with open(env_path, "w") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")
    print(f"\n✅ Config saved to {env_path}")

    # Auto-detect
    _run_auto_detect()

    print("\n🎉 Setup complete! Start the server with:")
    print("   python3 -m tooldns.cli serve")


def _run_auto_detect():
    """
    Auto-detect AI framework configs and offer selective ingestion.

    Scans for known configs (nanobot, openclaw), shows what was
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
        print("   Skipped. Run 'tooldns add' later.")
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
        config["name"] = input("   Source name (e.g., 'nanobot'): ").strip()
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
        print("No sources registered. Run 'tooldns add' to add one.")
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
        print("No tools indexed. Run 'tooldns add' to add a source.")
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
    Show ToolDNS system status: config, sources, tools, health.

    Displays a comprehensive overview of the current state including
    home directory, database stats, source health, and sample tools.
    """
    from tooldns.config import TOOLDNS_HOME
    home = TOOLDNS_HOME

    print_banner()
    print("📊 ToolDNS Status\n")

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
    print("   All good! ✅" if tool_count > 0 else "   ⚠️  No tools indexed. Run 'tooldns add'.")


def cmd_ingest():
    """Re-ingest all registered sources."""
    _, _, _, pipeline = get_components()

    print("⏳ Re-ingesting all sources...")
    total = pipeline.ingest_all()
    print(f"✅ Done! Total tools indexed: {total}")


def cmd_serve():
    """Start the ToolDNS API server."""
    import uvicorn
    print_banner()

    # Resolve the repo directory for correct main.py import
    repo_dir = Path(__file__).parent.parent.resolve()

    print(f"🚀 Starting ToolDNS server on {settings.host}:{settings.port}")
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
        install   Create ~/.tooldns, install deps, run setup
        update    Pull latest code and sync dependencies
        setup     Interactive config + auto-detect sources
        add       Add a tool source interactively
        sources   List registered sources
        tools     List indexed tools
        search    Search for a tool by query
        status    Show system status and health
        ingest    Re-ingest all sources
        serve     Start the API server
    """
    if len(sys.argv) < 2:
        print_banner()
        print("Usage: python3 -m tooldns.cli <command>\n")
        print("Commands:")
        print("  install   Create ~/.tooldns, install deps, run setup")
        print("  update    Pull latest code and sync dependencies")
        print("  setup     Interactive config + auto-detect sources")
        print("  add       Add a tool source interactively")
        print("  sources   List registered sources")
        print("  tools     List indexed tools [--source NAME]")
        print("  search    Search for a tool")
        print("  status    Show system status and health")
        print("  ingest    Re-ingest all sources")
        print("  serve     Start the API server")
        return

    cmd = sys.argv[1]

    if cmd == "install":
        cmd_install()
    elif cmd == "update":
        cmd_update()
    elif cmd == "setup":
        cmd_setup()
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
    else:
        print(f"Unknown command: {cmd}")
        print("Run 'python3 -m tooldns.cli' for help.")


if __name__ == "__main__":
    main()
