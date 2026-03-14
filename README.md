# ToolDNS

**DNS for AI Tools** — Search 10,000 tools. Return only the one you need.

> The standard way to give an AI agent tools (loading a massive MCP with 500 schemas) is like making someone read a 1,000-page encyclopedia every time you ask them a single question. ToolDNS fixes this.

## The Problem

When you connect an AI agent to tools via Composio, Zapier MCP, or similar platforms:

- 🐌 **Slow** — Hundreds of tool schemas bloat the context window
- 💸 **Expensive** — Every message costs more because the LLM reads all tool definitions
- 🤷 **Confused** — More tools = worse tool selection accuracy
- 📦 **Coupled** — Tied to one framework or provider

## The Solution

ToolDNS is a universal tool registry with semantic search routing. Point it at your MCP servers, skill files, or APIs — and when your LLM needs a tool, it queries ToolDNS to get back **only** the relevant tool schema.

```
Without ToolDNS:  LLM receives 500 tool schemas (50,000+ tokens) every message
With ToolDNS:     LLM searches → gets 1-2 relevant schemas (~200 tokens)
```

## Features

| Feature | Description |
|---------|-------------|
| 🔍 **Semantic Search** | Find tools by natural language, not exact names |
| 📊 **Token Tracking** | Real token counting with tiktoken, cost savings per search |
| 🏥 **Health Monitoring** | Auto-check if MCP servers are online/degraded/down |
| 🛒 **Marketplace** | One-click install popular MCP servers (GitHub, Slack, etc.) |
| 🎨 **Web Dashboard** | Browser UI for managing sources, browsing tools, viewing stats |
| 🔌 **MCP Server Mode** | Expose ToolDNS itself as an MCP server for any agent |
| ⚡ **FastMCP Integration** | Native FastMCP wrapper for easy agent integration |
| 📦 **Skill Management** | Create and manage skill files from the UI |
| 🔧 **Auto-Discovery** | Pull tools from any MCP config file automatically |

## How It Works

```
┌──────────────────┐     ┌─────────────────────┐     ┌──────────────┐
│  Tool Sources     │     │      ToolDNS         │     │  LLM Agent   │
│                   │     │                      │     │              │
│ • MCP Servers     │────▶│  1. Register tools   │◀────│  "I need a   │
│ • Config files    │     │  2. Embed & index    │     │   tool to    │
│ • Skill files     │     │  3. Semantic search   │────▶│   create a   │
│ • Custom tools    │     │  4. Return only the   │     │   github     │
│ • OpenAPI specs   │     │     relevant schema   │     │   issue"     │
└──────────────────┘     └─────────────────────┘     └──────────────┘
```

1. **Register sources** — Point ToolDNS at your MCP configs, skill directories, or custom tools
2. **Auto-discover** — ToolDNS connects to each MCP server and fetches all tool definitions
3. **Embed & index** — Each tool's description is embedded for semantic search (locally, no API cost)
4. **Search** — When an LLM needs a tool, it queries ToolDNS with natural language
5. **Return** — ToolDNS returns only the 1-2 most relevant tool schemas

## Quick Start

### Installation

```bash
git clone https://github.com/syedfahimdev/tooldns.git
cd tooldns
pip install -r requirements.txt
```

### Interactive Setup

```bash
python -m tooldns.cli setup
```

This walks you through:
- Generating an API key
- Configuring the server
- Adding your first tool source

### Add Tool Sources

```bash
# Interactive mode
python -m tooldns.cli add

# Or add specific source types:
```

#### From an MCP Config File (e.g., nanobot's config.json)
```bash
# The CLI will ask for the file path and JSON key
# Example: path = ~/.nanobot/config.json, key = tools.mcpServers
python -m tooldns.cli add
# Choose option 1 (MCP Config File)
```

#### From a Skill Directory
```bash
# Point to a folder of .md skill files
python -m tooldns.cli add
# Choose option 4 (Skill Directory)
```

### Start the Server

```bash
# Via CLI
python -m tooldns.cli serve

# Or directly
python main.py

# Or with uvicorn
uvicorn main:app --port 8787
```

### Web Dashboard

The server includes a built-in web UI:

```
http://localhost:8787/ui
```

Features:
- Dashboard: Overview of indexed tools, recent searches
- Sources: Add/remove/edit MCP server sources
- Tools: Browse all indexed tools with search
- Marketplace: One-click install popular MCP servers
- Health: Monitor which tools are online/offline
- Stats: Token savings, search analytics
- Settings: Configure API key, refresh interval

### Search for Tools

```bash
# Via CLI
python -m tooldns.cli search "create a github issue"

# Via API
curl -X POST http://localhost:8787/v1/search \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "create a github issue", "top_k": 2}'
```

## API Reference

### `POST /v1/search` — Find the right tool

The core endpoint. Send a natural language query, get back the matching tool(s).

**Request:**
```json
{
  "query": "create a github issue about the login bug",
  "top_k": 2,
  "threshold": 0.5
}
```

**Response:**
```json
{
  "results": [
    {
      "id": "composio__GITHUB_CREATE_ISSUE",
      "name": "GITHUB_CREATE_ISSUE",
      "description": "Create a new issue in a GitHub repository",
      "confidence": 0.94,
      "input_schema": { ... },
      "source": "composio",
      "how_to_call": {
        "type": "mcp",
        "server": "composio",
        "tool_name": "GITHUB_CREATE_ISSUE"
      }
    }
  ],
  "total_tools_indexed": 523,
  "tokens_saved": 62400,
  "search_time_ms": 12.3
}
```

### `POST /v1/sources` — Register a tool source

```json
{
  "type": "mcp_config",
  "name": "nanobot",
  "path": "~/.nanobot/config.json",
  "config_key": "tools.mcpServers"
}
```

### `GET /v1/sources` — List registered sources

### `GET /v1/tools` — List all indexed tools

### `POST /v1/ingest` — Refresh all sources

### `DELETE /v1/sources/{id}` — Remove a source

### `GET /v1/health` — Check tool/source health status

### `GET /v1/stats` — Get token savings and usage analytics

### API Docs

Full interactive API documentation is available at `http://localhost:8787/docs` when the server is running.

## CLI Commands

| Command | Description |
|---------|-------------|
| `python -m tooldns.cli setup` | Interactive first-time setup |
| `python -m tooldns.cli add` | Add a tool source interactively |
| `python -m tooldns.cli sources` | List registered sources |
| `python -m tooldns.cli tools` | List all indexed tools |
| `python -m tooldns.cli search "query"` | Search for a tool |
| `python -m tooldns.cli ingest` | Re-ingest all sources |
| `python -m tooldns.cli serve` | Start the API server |
| `python -m tooldns.cli health` | Check health of all sources |
| `python -m tooldns.cli integrate` | Wizard to integrate with nanobot/openclaw |

## Configuration

All configuration is via environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOLDNS_API_KEY` | `td_dev_key` | API key for authentication |
| `TOOLDNS_HOST` | `0.0.0.0` | Server bind address |
| `TOOLDNS_PORT` | `8787` | Server port |
| `TOOLDNS_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformer model |
| `TOOLDNS_DB_PATH` | `./tooldns.db` | SQLite database path |
| `TOOLDNS_REFRESH_INTERVAL` | `15` | Auto-refresh interval (minutes) |
| `TOOLDNS_LOG_LEVEL` | `INFO` | Log level |

## MCP Server Mode

ToolDNS can expose itself as an MCP server, giving any MCP-capable agent access to its tool registry:

```python
# In your nanobot / openclaw / mcporter config:
"tooldns": {
    "command": "python3",
    "args": ["-m", "tooldns.mcp_server"]
}
```

This exposes 5 tools:
- `search_tools` — find tools by natural language
- `get_tool` — get full schema + skill instructions
- `call_tool` — execute a tool through ToolDNS
- `register_mcp_server` — add a new MCP server on the fly
- `create_skill` — create a new skill file

Plus two live MCP resources:
- `tooldns://tools` — browse all indexed tools
- `tooldns://sources` — list all registered sources

## LLM Integration Pattern

Give your LLM a single "search" tool instead of hundreds of actual tools:

```python
# The only tool your LLM needs
tools = [{
    "name": "tooldns_search",
    "description": "Search for the right tool to accomplish a task. "
                   "Use this before attempting any external action.",
    "parameters": {
        "query": {"type": "string", "description": "What tool you need"}
    }
}]

# When the LLM calls tooldns_search:
# 1. Forward the query to ToolDNS
# 2. Get back the relevant tool schema
# 3. LLM can now call the actual tool with the returned schema
```

## Architecture

```
tooldns/
├── main.py              # FastAPI server entry point
├── tooldns/
│   ├── __init__.py
│   ├── config.py        # Settings from environment variables
│   ├── models.py        # Pydantic data models (universal tool schema)
│   ├── database.py      # SQLite storage for tools and sources
│   ├── embedder.py     # Sentence-transformers embedding engine
│   ├── fetcher.py      # MCP protocol client (stdio + HTTP transports)
│   ├── ingestion.py    # Multi-source ingestion pipeline
│   ├── search.py       # Semantic search with cosine similarity
│   ├── auth.py         # API key authentication
│   ├── api.py          # FastAPI route handlers
│   ├── cli.py          # Interactive command-line interface
│   ├── mcp_server.py   # FastMCP wrapper (expose as MCP server)
│   ├── health.py       # Tool/source health monitoring
│   ├── marketplace.py  # Curated MCP server catalog
│   ├── tokens.py       # Token counting and cost estimation
│   ├── integrate.py    # Wizard for nanobot/openclaw integration
│   ├── ui.py           # Web dashboard routes
│   └── static/         # CSS, JS for web UI
├── templates/           # Jinja2 templates for web UI
├── requirements.txt
├── .env.example
└── .gitignore
```

## Supported Source Types

| Source Type | Description | How It Works |
|-------------|-------------|--------------|
| `mcp_config` | Config file with MCP servers | Reads JSON, discovers all listed MCP servers, fetches their tools |
| `mcp_stdio` | Single stdio MCP server | Spawns subprocess, communicates via stdin/stdout |
| `mcp_http` | Single HTTP MCP server | Makes HTTP POST requests (Streamable HTTP transport) |
| `skill_directory` | Directory of skill .md files | Parses YAML headers and TEMPLATE sections |
| `custom` | Single custom tool | User provides name, description, and schema |

## Token Savings

ToolDNS tracks real token usage using tiktoken (cl100k_base encoding, ~5% accuracy for all major LLMs). Each search response includes:

- `tokens_saved` — tokens not sent to LLM by using semantic search
- `search_time_ms` — how fast the search ran

This helps you quantify the cost savings of using ToolDNS vs loading all tool schemas.

## Health Monitoring

ToolDNS periodically checks whether registered MCP servers are reachable:

- **HTTP MCP servers**: Send a ping, check HTTP 200
- **stdio MCP servers**: Use "staleness" heuristic (if refreshed within 2× interval = healthy)
- **Skill directories**: Always healthy (local files)

Check health via API: `GET /v1/health` or web UI: `/ui/health`

## Marketplace

Built-in catalog of popular MCP servers with one-click install:

- GitHub, Git, Filesystem
- Browser automation (Playwright, Puppeteer)
- Slack, Discord, Telegram
- Search (Brave, SerpAPI)
- Data (Supabase, PostgreSQL)
- Cloud (AWS, GCP, Azure)
- AI (OpenAI, Anthropic, HuggingFace)

## Future Improvements

### Planned Features
- [ ] **Auto-refresh scheduler** — Periodically re-ingest sources on a cron schedule (already partially implemented)
- [ ] **SDK packages** — Python and TypeScript client libraries
- [ ] **Multi-tenant support** — Team workspaces with shared tool registries
- [ ] **Community marketplace** — Share and discover tool registries publicly
- [ ] **Webhook support** — Get notified when sources update their tool lists
- [ ] **Vector DB upgrade** — Migrate from SQLite to Qdrant/pgvector for larger indexes

### Performance Targets
- Search latency: <50ms for 10,000 tools
- Embedding: <10ms per query
- Ingestion: Handle 1,000+ tools per source

## License

MIT
