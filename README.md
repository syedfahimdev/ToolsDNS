# ToolsDNS

**DNS for AI Tools** — Search 1,700+ tools. Return only the one you need.

[![CI](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/ci.yml/badge.svg)](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/ci.yml)
[![Security](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/security.yml/badge.svg)](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/security.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-purple.svg)](LICENSE)

> The standard way to give an AI agent tools (loading a massive MCP with 500 schemas) is like making someone read a 1,000-page encyclopedia every time you ask them a single question. ToolsDNS fixes this.

---

## The Problem

When you connect an AI agent to tools via Composio, Zapier MCP, or similar platforms:

- 🐌 **Slow** — Hundreds of tool schemas bloat the context window
- 💸 **Expensive** — Every message costs more because the LLM reads all tool definitions
- 🤷 **Confused** — More tools = worse tool selection accuracy
- 📦 **Coupled** — Tied to one framework or provider

## The Solution

ToolsDNS is a universal tool registry with semantic search routing. Point it at your MCP servers, skill files, or APIs — when your LLM needs a tool, it queries ToolsDNS to get back **only** the relevant tool schema.

```
Without ToolsDNS:  LLM receives 500 tool schemas (50,000+ tokens) every message
With ToolsDNS:     LLM searches → gets 1-2 relevant schemas (~200 tokens)
```

---

## Features

| Feature | Description |
|---------|-------------|
| 🔍 **Semantic Search** | Find tools by natural language, not exact names |
| 📋 **Skill Listing** | `list_skills` tool — agents instantly see all your skills |
| 📊 **Token Tracking** | Real token counting with cost savings per search |
| 🏥 **Health Monitoring** | Auto-check if MCP servers are online/degraded/down |
| 🛒 **Marketplace** | One-click install popular MCP servers (GitHub, Slack, etc.) |
| 🎨 **Web Dashboard** | Full browser UI — manage everything without touching code |
| 🔌 **MCP Server Mode** | Expose ToolsDNS itself as an MCP server for any agent |
| 📦 **Skill Management** | Create, read, update skills from the UI or API |
| 🔧 **Auto-Discovery** | Pull tools from any URL (Smithery, npm, GitHub, HTTP MCP) |
| 🔑 **API Key Manager** | Per-key usage tracking, monthly limits, client portal |
| 🚀 **1-Click Deploy** | Built-in deploy guide for Railway, Render, Fly.io, Docker |
| 🏷️ **White-Label** | Brand it as your own via env vars — no code changes |

---

## How It Works

```
┌──────────────────┐     ┌──────────────────────┐     ┌──────────────┐
│  Tool Sources     │     │      ToolsDNS          │     │  LLM Agent   │
│                   │     │                       │     │              │
│ • MCP Servers     │────▶│  1. Register tools    │◀────│  "What skills│
│ • Config files    │     │  2. Embed & index     │     │   do I have?"│
│ • Skill files     │     │  3. list_skills()      │────▶│              │
│ • Custom tools    │     │  4. search_tools()    │     │  "Create a   │
│ • OpenAPI specs   │     │  5. Return only the   │────▶│   work order"│
└──────────────────┘     │     relevant schema   │     └──────────────┘
                          └──────────────────────┘
```

1. **Register sources** — Point ToolsDNS at your MCP configs, skill directories, or custom tools
2. **Auto-discover** — ToolsDNS connects to each MCP server and fetches all tool definitions
3. **Embed & index** — Each tool's description is embedded for semantic search (locally, no API cost)
4. **list_skills / search** — Agent calls `list_skills()` to see capabilities, or `search_tools(query)` for any task
5. **Return** — ToolsDNS returns only the relevant tool schema + exactly how to call it

---

## Quick Start

### Install

```bash
git clone https://github.com/syedfahimdev/ToolsDNS.git
cd ToolsDNS
pip install -e .
```

### Run

```bash
# Interactive setup (recommended for first time)
toolsdns setup

# Start the server
toolsdns serve

# Or with Docker (recommended for production)
docker compose up -d
```

### Web Dashboard

```
http://localhost:8787/ui
```

All management through the UI — no config file editing needed:

| Page | What you can do |
|------|----------------|
| **Dashboard** | Overview, tool count, recent searches, onboarding wizard |
| **Add Tools** | Marketplace — one-click install popular MCP servers |
| **Browse Tools** | Search all 1,700+ indexed tools |
| **Sources** | Add/remove/edit MCP server sources, auto-discover from URL |
| **Savings** | Token savings tracker, shareable savings card |
| **API Keys** | Create sub-keys, set monthly limits, track usage |
| **Settings** | API key, branding (app name, email, colors), env vars |
| **Deploy** | Step-by-step guide for Railway, Render, Fly.io, Docker |
| **Client Portal** | Self-service page for your API key customers |

---

## API Reference

### `POST /v1/search` — Find the right tool

```bash
curl -X POST http://localhost:8787/v1/search \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "create a github issue", "limit": 2}'
```

```json
{
  "results": [{
    "name": "GITHUB_CREATE_ISSUE",
    "description": "Creates a new issue in a GitHub repository",
    "confidence": 0.94,
    "how_to_call": {
      "type": "mcp",
      "server": "composio",
      "instruction": "Call this tool via the 'composio' MCP server."
    }
  }],
  "total_tools_indexed": 1709,
  "tokens_saved": 284710
}
```

### `GET /v1/skills` — List all skills

```bash
curl http://localhost:8787/v1/skills \
  -H "Authorization: Bearer td_your_key"
```

```json
{
  "skills": [
    {"name": "everi-work-order", "description": "Create Excel work orders..."},
    {"name": "work-email-assistant", "description": "Email automation for..."}
  ],
  "total": 5
}
```

### `GET /v1/skills/{name}` — Get full skill instructions

```bash
curl http://localhost:8787/v1/skills/everi-work-order \
  -H "Authorization: Bearer td_your_key"
```

Returns the full `SKILL.md` content — the agent follows these instructions.

### `POST /v1/discover` — Auto-discover from any URL

```bash
curl -X POST http://localhost:8787/v1/discover \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://smithery.ai/server/@modelcontextprotocol/server-github"}'
```

Supports: Smithery URLs, npm packages, GitHub repos, HTTP MCP endpoints.

### `GET /v1/health` — Server health

```bash
curl http://localhost:8787/v1/health \
  -H "Authorization: Bearer td_your_key"
```

---

## MCP Server Integration

ToolsDNS exposes itself as an MCP server. Add it to any agent (nanobot, OpenClaw, etc.):

```json
{
  "mcpServers": {
    "tooldns": {
      "command": "python3",
      "args": ["-m", "tooldns.mcp_server"]
    }
  }
}
```

Tools exposed via MCP:

| Tool | Description |
|------|-------------|
| `list_skills` | List all skills — call when asked "what can you do?" |
| `search_tools(query)` | Semantic search across all indexed tools |
| `get_tool(id)` | Get full schema for a specific tool |
| `call_tool(id, args)` | Execute a tool through ToolsDNS |
| `read_skill(name)` | Get full SKILL.md instructions for a skill |
| `create_skill(...)` | Create a new skill file |
| `register_mcp_server(...)` | Add a new MCP server to the index |

MCP resources:
- `tooldns://tools` — browse all indexed tools
- `tooldns://sources` — list all registered sources

---

## Configuration

All configuration via environment variables (or `~/.tooldns/.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOLDNS_API_KEY` | `td_dev_key` | API key for authentication |
| `TOOLDNS_HOST` | `0.0.0.0` | Server bind address |
| `TOOLDNS_PORT` | `8787` | Server port |
| `TOOLDNS_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformer model |
| `TOOLDNS_REFRESH_INTERVAL` | `15` | Auto-refresh interval (minutes, 0=off) |
| `TOOLDNS_LOG_LEVEL` | `INFO` | Log level |
| `TOOLDNS_WEBHOOK_URL` | *(empty)* | URL to POST health alerts to |
| `TOOLDNS_WEBHOOK_SECRET` | *(empty)* | Sent as `X-ToolsDNS-Secret` header |
| `TOOLDNS_APP_NAME` | `ToolsDNS` | Brand name (white-label) |
| `TOOLDNS_APP_TAGLINE` | `DNS for AI Tools` | Tagline shown in UI |
| `TOOLDNS_CONTACT_EMAIL` | `hello@toolsdns.com` | Support email |
| `TOOLDNS_GITHUB_URL` | GitHub repo URL | Linked in footer |

---

## Docker / Production

### Docker Compose

The recommended way to run ToolsDNS in production. Your `~/.tooldns` folder is bind-mounted so config, skills, tools, and the database are shared between the host and container.

```bash
docker compose up -d
```

Your `~/.tooldns` folder works exactly the same whether you're running native or in Docker:
- Edit `~/.tooldns/config.json` on the host → hot-reload picks it up in ~1s
- Skills in `~/.tooldns/skills/` are served from the host
- Database at `~/.tooldns/tooldns.db` persists across container restarts

### Railway / Render / Fly.io

See the **🚀 Deploy** page in the web UI at `/ui/deploy` for step-by-step guides with all environment variables pre-filled.

### Hot Reload

ToolsDNS watches `~/.tooldns/config.json` with OS-level file notifications. When you save a change — adding a new MCP server, a new skill — re-ingestion starts automatically within ~1 second. No restart needed.

---

## Webhooks / Alerts

```bash
# ~/.tooldns/.env
TOOLDNS_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
TOOLDNS_WEBHOOK_SECRET=my-signing-secret   # optional
```

ToolsDNS POSTs a JSON payload to your webhook when source health changes (online → down, etc.). The secret is sent as `X-ToolsDNS-Secret` so your endpoint can verify the request came from ToolsDNS.

---

## Project Structure

```
ToolsDNS/
├── main.py              # FastAPI app entry point
├── docker-compose.yml   # Bind-mounts ~/.tooldns, exposes port 8787
├── pyproject.toml       # Package config (CLI entry: toolsdns)
├── tooldns/             # Python package (import name)
│   ├── api.py           # REST API routes (/v1/*)
│   ├── ui.py            # Web UI routes (/ui/*)
│   ├── auth.py          # API key auth + multi-tenant sub-keys
│   ├── cli.py           # CLI (toolsdns setup / serve / add / search)
│   ├── config.py        # Settings with env var overrides
│   ├── database.py      # SQLite with FTS5 + vector search
│   ├── discover.py      # Auto-discover from URL (Smithery/npm/GitHub/HTTP)
│   ├── embedder.py      # Sentence-transformer embeddings
│   ├── fetcher.py       # MCP server tool fetching
│   ├── health.py        # Source health monitoring
│   ├── ingestion.py     # Tool indexing pipeline
│   ├── marketplace.py   # Pre-built MCP server catalog
│   ├── mcp_server.py    # FastMCP server (exposes ToolsDNS as MCP)
│   ├── models.py        # Pydantic models
│   ├── search.py        # Hybrid semantic + BM25 search
│   ├── tokens.py        # Token counting + cost calculation
│   ├── templates/       # Jinja2 HTML templates
│   └── static/          # CSS + JS
└── .github/
    ├── workflows/ci.yml          # CI — import checks, tests, branding
    └── workflows/security.yml    # Security — Bandit, CVE scan, secret check
```

---

## License

**AGPL-3.0** for open-source use. See [LICENSE](LICENSE).

For commercial/proprietary use (closed-source products, white-label resale), contact [syed@toolsdns.com](mailto:syed@toolsdns.com) for a commercial license.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). PRs welcome — especially new marketplace skills and MCP server connectors.

**Contact:** [hello@toolsdns.com](mailto:hello@toolsdns.com) | [GitHub Issues](https://github.com/syedfahimdev/ToolsDNS/issues)
