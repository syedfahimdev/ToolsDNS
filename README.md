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

# Or with Docker (recommended for production)
docker compose up -d
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

## Docker

The recommended way to run ToolDNS in production. Your `~/.tooldns` folder is bind-mounted so config, skills, tools, and the database are shared between the host and container — edit anything on the host and it's picked up live.

```bash
# Start
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

**Your `~/.tooldns` folder** works exactly the same whether you're running native or in Docker:
- Edit `~/.tooldns/config.json` on the host → hot-reload picks it up in ~1s inside the container
- Skills in `~/.tooldns/skills/` are served from the host
- Database at `~/.tooldns/tooldns.db` persists across container restarts

Pass environment variables or API keys via `docker-compose.yml` or an `env_file` pointing to `~/.tooldns/.env`.

## Configuration

All configuration is via environment variables (or `~/.tooldns/.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOLDNS_API_KEY` | `td_dev_key` | API key for authentication |
| `TOOLDNS_HOST` | `0.0.0.0` | Server bind address |
| `TOOLDNS_PORT` | `8787` | Server port |
| `TOOLDNS_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformer model |
| `TOOLDNS_DB_PATH` | `~/.tooldns/tooldns.db` | SQLite database path |
| `TOOLDNS_REFRESH_INTERVAL` | `15` | Auto-refresh interval (minutes, 0=off) |
| `TOOLDNS_LOG_LEVEL` | `INFO` | Log level |
| `TOOLDNS_WEBHOOK_URL` | *(empty)* | URL to POST health alerts to |
| `TOOLDNS_WEBHOOK_SECRET` | *(empty)* | Sent as `X-ToolDNS-Secret` header |

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

## Hot Reload

ToolDNS watches `~/.tooldns/config.json` with OS-level file notifications (inotify on Linux, kqueue on macOS). When you save a change — adding a new MCP server, a new skill path, etc. — re-ingestion starts automatically within ~1 second. No restart needed.

```bash
# Example: add a server to config.json, it's indexed within seconds
nano ~/.tooldns/config.json  # save → watch the logs
tail -f ~/.tooldns/tooldns.log | grep -E "changed|Hot-reload"
```

## Webhook Alerts

Get notified when a source goes down or recovers. Set the URL once:

```bash
# ~/.tooldns/.env
TOOLDNS_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
TOOLDNS_WEBHOOK_SECRET=my-signing-secret   # optional
```

ToolDNS POSTs this JSON when any source changes status:

```json
{
  "event": "source_health_change",
  "source": "composio",
  "previous_status": "healthy",
  "current_status": "down",
  "timestamp": "2026-03-14T22:03:15.059512Z"
}
```

The secret is sent as the `X-ToolDNS-Secret` header so your endpoint can verify the request came from ToolDNS.

**Works with any HTTP endpoint** — Slack incoming webhooks, Discord webhooks, PagerDuty Events API, or your own endpoint. Only fires on transitions (healthy→down, down→healthy, etc.), not on every health check.

## Architecture

```
tooldns/
├── main.py              # FastAPI entry point, lifespan, hot-reload watcher
├── Dockerfile           # Production container image
├── docker-compose.yml   # Bind-mounts ~/.tooldns, exposes port 8787
├── tooldns/
│   ├── config.py        # Settings (env vars, webhook URL, etc.)
│   ├── models.py        # Pydantic data models (universal tool schema)
│   ├── database.py      # SQLite: tools, sources, search log, embedding cache
│   ├── embedder.py      # Sentence-transformers embedding engine
│   ├── fetcher.py       # MCP protocol client (stdio + HTTP transports)
│   ├── ingestion.py     # Multi-source ingest pipeline (batch upserts)
│   ├── search.py        # Hybrid semantic + BM25 search
│   ├── auth.py          # API key authentication
│   ├── api.py           # REST API routes
│   ├── cli.py           # Interactive CLI
│   ├── mcp_server.py    # FastMCP wrapper (expose as MCP server)
│   ├── health.py        # Source health monitor + webhook firing
│   ├── marketplace.py   # Curated MCP server + skill catalog
│   ├── tokens.py        # Token counting and cost estimation
│   ├── integrate.py     # Wizard for nanobot/openclaw integration
│   ├── ui.py            # Web dashboard + marketplace routes
│   └── static/          # CSS, JS for web UI
├── templates/           # Jinja2 templates for web UI
├── requirements.txt
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

ToolDNS tracks real token usage using tiktoken (cl100k_base encoding). Each search response includes:

- `tokens_saved` — tokens not sent to LLM by using semantic search
- `search_time_ms` — how fast the search ran

View cumulative savings at `/ui/stats` or `GET /v1/stats`.

## Health Monitoring

ToolDNS periodically checks whether registered MCP servers are reachable:

- **HTTP MCP servers**: Send a ping, check HTTP 200
- **stdio MCP servers**: Use "staleness" heuristic (if refreshed within 2× interval = healthy)
- **Skill directories**: Always healthy (local files)

Check health via API: `GET /v1/health` or web UI: `/ui/health`. Set `TOOLDNS_WEBHOOK_URL` to get Slack/Discord alerts on status changes.

## Marketplace

Built-in catalog of 30+ popular MCP servers with one-click install:

- Dev: GitHub, Git, GitLab, Linear, Sentry
- Browser: Playwright, Puppeteer, E2B
- Communication: Slack, Notion, Gmail
- Search: Brave, Tavily, Exa
- Data: PostgreSQL, SQLite, Supabase
- Cloud: Cloudflare, Docker, Kubernetes, AWS
- AI: Sequential Thinking, Hugging Face, Context7

## Performance Targets
- Search latency: <50ms for 10,000 tools
- Embedding: <10ms per query (cached after first run)
- Ingestion: batch upserts via single SQLite transaction per source

## License

MIT
