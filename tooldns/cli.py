"""
cli.py — Interactive CLI for ToolDNS.

Provides a command-line interface for setting up, managing,
and using ToolDNS without running the HTTP server.

Commands:
    tooldns setup       — Interactive first-time setup
    tooldns add         — Add a source interactively
    tooldns sources     — List registered sources
    tooldns tools       — List all indexed tools
    tooldns search      — Search for a tool
    tooldns ingest      — Re-ingest all sources
    tooldns serve       — Start the API server

Usage:
    python -m tooldns.cli setup
    python -m tooldns.cli add
    python -m tooldns.cli search "create a github issue"
    python -m tooldns.cli serve
"""

import sys
import json
import os
import secrets
from pathlib import Path
from tooldns.config import settings, logger
from tooldns.models import SourceType


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


def cmd_setup():
    """
    Interactive first-time setup wizard.

    Walks the user through:
    1. Generating an API key
    2. Setting the database path
    3. Choosing whether to add a first source
    4. Writing the .env file
    """
    print_banner()
    print("Welcome to ToolDNS setup!\n")

    env_path = Path(__file__).parent.parent / ".env"
    env_vars = {}

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

    # Database
    print("\n3️⃣  Database Path")
    db = input(f"   Path [{settings.db_path}]: ").strip()
    if db:
        env_vars["TOOLDNS_DB_PATH"] = db

    # Write .env
    with open(env_path, "w") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")
    print(f"\n✅ Config saved to {env_path}")

    # Offer to add first source
    print("\n4️⃣  Add your first tool source?")
    add = input("   Add a source now? [Y/n]: ").strip().lower()
    if add != "n":
        cmd_add()

    print("\n🎉 Setup complete! Start the server with:")
    print("   python -m tooldns.cli serve")
    print("   or: python main.py")


def cmd_add():
    """
    Interactive source addition wizard.

    Walks the user through adding a tool source with a menu
    of supported types. Immediately ingests the source after adding.
    """
    db, embedder, search, pipeline = get_components()

    print("\n📦 Add a Tool Source")
    print("   What type of source?")
    print("")
    print("   1) MCP Config File   — Read all MCP servers from a config.json")
    print("   2) MCP Server (stdio) — Connect to a local MCP server process")
    print("   3) MCP Server (HTTP)  — Connect to a remote MCP server URL")
    print("   4) Skill Directory    — Read skill .md files from a folder")
    print("   5) Custom Tool        — Register a single tool manually")
    print("")

    choice = input("   Choice [1-5]: ").strip()

    config = {}

    if choice == "1":
        config["type"] = SourceType.MCP_CONFIG
        config["name"] = input("   Source name (e.g., 'nanobot'): ").strip()
        config["path"] = input("   Config file path (e.g., ~/.nanobot/config.json): ").strip()
        config["config_key"] = input("   JSON path to MCP servers [tools.mcpServers]: ").strip() or "tools.mcpServers"

    elif choice == "2":
        config["type"] = SourceType.MCP_STDIO
        config["name"] = input("   Source name (e.g., 'my-skills'): ").strip()
        config["command"] = input("   Command (e.g., python3): ").strip()
        args_str = input("   Arguments (space-separated): ").strip()
        config["args"] = args_str.split() if args_str else []

    elif choice == "3":
        config["type"] = SourceType.MCP_HTTP
        config["name"] = input("   Source name (e.g., 'composio'): ").strip()
        config["url"] = input("   Server URL: ").strip()
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
    print(f"🚀 Starting ToolDNS server on {settings.host}:{settings.port}")
    print(f"   API docs: http://localhost:{settings.port}/docs\n")
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
        python -m tooldns.cli <command> [args]

    Commands:
        setup     Interactive first-time setup
        add       Add a tool source interactively
        sources   List registered sources
        tools     List indexed tools
        search    Search for a tool by query
        ingest    Re-ingest all sources
        serve     Start the API server
    """
    if len(sys.argv) < 2:
        print_banner()
        print("Usage: python -m tooldns.cli <command>\n")
        print("Commands:")
        print("  setup     Interactive first-time setup")
        print("  add       Add a tool source interactively")
        print("  sources   List registered sources")
        print("  tools     List indexed tools [--source NAME]")
        print("  search    Search for a tool")
        print("  ingest    Re-ingest all sources")
        print("  serve     Start the API server")
        return

    cmd = sys.argv[1]

    if cmd == "setup":
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
    elif cmd == "ingest":
        cmd_ingest()
    elif cmd == "serve":
        cmd_serve()
    else:
        print(f"Unknown command: {cmd}")
        print("Run 'python -m tooldns.cli' for help.")


if __name__ == "__main__":
    main()
