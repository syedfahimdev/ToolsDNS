<div align="center">

# ToolsDNS

**DNS for AI Tools** — Semantic search over thousands of MCP tools. Return only what the agent needs.

[![CI](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/ci.yml/badge.svg)](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/ci.yml)
[![Security](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/security.yml/badge.svg)](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/security.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-purple.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[🌐 Website](https://toolsdns.com) · [📖 Docs](#api-reference) · [🚀 Deploy](#deploy) · [🤝 Contribute](#contributing)

</div>

---

## The Problem

Loading 500 tool schemas into every LLM message is like making someone memorize a phone book before every call.

| Without ToolsDNS | With ToolsDNS |
|---|---|
| 500 schemas loaded every message | 1–3 relevant schemas returned |
| 50,000+ tokens per request | ~200 tokens per request |
| Agent confused by too many choices | Agent gets exactly what it needs |
| Tied to one MCP provider | Works with any MCP server, skill, or API |

> **Real numbers from production:** searching 1,709 tools returns ~3 results, saving **284,000+ tokens per search** — roughly **$0.85 per query** at Claude Sonnet pricing.

---

## What ToolsDNS Does

```
┌─────────────────┐     ┌──────────────────────┐     ┌──────────────┐
│  Your Tools      │     │      ToolsDNS          │     │  AI Agent    │
│                  │     │                       │     │              │
│ • MCP Servers    │────▶│  Index + Embed        │     │ "What can    │
│ • Skill files    │     │  1,700+ tool schemas  │◀────│  you do?"    │
│ • Config files   │     │                       │     │              │
│ • Custom tools   │     │  list_skills()        │────▶│ (sees your   │
│ • OpenAPI specs  │     │  search_tools(query)  │     │  skills)     │
└─────────────────┘     │  → returns 1-3 tools  │────▶│              │
                         └──────────────────────┘     │ "Send email  │
                                                       │  to Alice"   │
                                                       └──────────────┘
```

1. **Register** your MCP servers, skill directories, or custom tools
2. **Index** — ToolsDNS connects to each server and embeds every tool description locally (no API cost)
3. **Search** — Agent calls `list_skills()` to see capabilities, or `search_tools("send email")` for any task
4. **Return** — Only the matching schema + exactly how to call it

---

## Features

| Feature | Description |
|---|---|
| 🔍 **Semantic Search** | Natural language queries — "send email" finds `GMAIL_SEND_EMAIL` even without exact name match |
| ⚡ **Hybrid Scoring** | Semantic similarity (70%) + BM25 keyword (30%) — exact names AND fuzzy descriptions both work |
| 📋 **Skill Listing** | `list_skills()` MCP tool — agents instantly discover all your custom skills |
| 📊 **Token Savings Tracker** | Real token counting with per-model cost savings (not estimates) |
| 🏥 **Health Monitoring** | Auto-checks if MCP servers are online/degraded/down — webhooks on status change |
| 🛒 **Marketplace** | One-click install for GitHub, Slack, Gmail, Notion, and 30+ popular MCP servers |
| 🎨 **Web Dashboard** | Full browser UI — manage sources, search tools, view savings, generate API keys |
| 🔌 **MCP Protocol** | Exposes itself as an MCP server — plug into nanobot, Claude Desktop, any agent |
| 📦 **Skill Management** | Create, read, update skill files from UI or API |
| 🔧 **Auto-Discovery** | Point at any Smithery URL, npm package, GitHub repo, or HTTP MCP endpoint |
| 🔑 **API Key Manager** | Multi-tenant sub-keys with per-key usage tracking and monthly limits |
| 🏷️ **White-Label Ready** | Rebrand via env vars — your name, your domain, no code changes |
| 🚀 **One-Command Deploy** | `curl | bash` installer for any Ubuntu/Debian VPS |
| 🔄 **Hot Reload** | Edit `config.json` → tools re-index in ~1 second, no restart needed |
| 🏷️ **Tool Categories** | 15 categories auto-assigned (Dev & Code, Communication, AI & Agents, etc.) |
| 🔒 **Security First** | Bandit static analysis + CVE scanning in CI, API key auth on all endpoints |

---

## Quick Start

### Option 1 — One-Command Install (VPS / Linux)

```bash
curl -sSL https://raw.githubusercontent.com/syedfahimdev/ToolsDNS/master/deploy.sh | sudo bash
```

Installs Python env, configures systemd service, generates API key, and verifies health. Done in ~60 seconds.

### Option 2 — Local Dev

```bash
git clone https://github.com/syedfahimdev/ToolsDNS.git
cd ToolsDNS
pip install -e .
toolsdns serve
```

Open **http://localhost:8787/ui**

### Option 3 — Docker

```bash
docker compose up -d
```

Your `~/.tooldns/` folder is bind-mounted — config, skills, database all persist on the host.

---

## Web Dashboard

Everything manageable through the browser — no config editing required:

| Page | What you can do |
|---|---|
| **Dashboard** | Tool count, recent searches, savings summary, onboarding wizard |
| **Add Tools** | Marketplace — one-click install 30+ popular MCP servers |
| **Browse Tools** | Search and filter 1,700+ indexed tools by category, source, keyword |
| **Sources** | Add/remove/edit MCP server sources, auto-discover from any URL |
| **Savings** | Token savings tracker with shareable savings card image |
| **API Keys** | Create sub-keys, set monthly limits, track per-key usage |
| **Settings** | API key, branding (app name, tagline, email), env var editor |
| **Deploy** | Step-by-step guides for Railway, Render, Fly.io, Docker + HTTPS |
| **Client Portal** | Self-service page for your API key customers |

---

## MCP Integration

Add ToolsDNS to any agent that supports MCP servers:

**nanobot / Claude Desktop / any MCP client:**

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

**Tools your agent gets:**

| MCP Tool | When to call it |
|---|---|
| `list_skills()` | User asks "what can you do?" or "what skills do you have?" |
| `search_tools(query)` | Before using any tool — find what's available |
| `get_tool(id)` | Need full schema for a specific tool |
| `call_tool(id, args)` | Execute a tool through ToolsDNS |
| `read_skill(name)` | Get full SKILL.md instructions before running a skill |
| `create_skill(...)` | Create a new skill file |
| `update_skill(...)` | Edit an existing skill |
| `register_mcp_server(...)` | Add a new MCP server to the index |

**Example agent workflow:**

```
User: "Create a work order for the maintenance team"

Agent → list_skills()                    # What skills exist?
      → sees "everi-work-order" listed
      → read_skill("everi-work-order")   # Get full instructions
      → follows SKILL.md instructions
      → creates the Excel work order ✓
```

---

## API Reference

All endpoints require `Authorization: Bearer <your_api_key>` (except `/health`).

### Search tools

```bash
curl -X POST https://api.toolsdns.com/v1/search \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "create a github issue", "top_k": 3}'
```

```json
{
  "results": [{
    "name": "GITHUB_CREATE_ISSUE",
    "description": "Creates a new issue in a GitHub repository",
    "confidence": 0.94,
    "category": "Dev & Code",
    "how_to_call": {
      "type": "mcp",
      "server": "composio",
      "instruction": "Call this tool via the 'composio' MCP server."
    }
  }],
  "total_tools_indexed": 1709,
  "tokens_saved": 284710,
  "search_time_ms": 42.3
}
```

### List all tools

```bash
curl "https://api.toolsdns.com/v1/tools?category=Dev%20%26%20Code&limit=50" \
  -H "Authorization: Bearer td_your_key"
```

### List skills

```bash
curl https://api.toolsdns.com/v1/skills \
  -H "Authorization: Bearer td_your_key"
```

### Get a skill's full instructions

```bash
curl https://api.toolsdns.com/v1/skills/everi-work-order \
  -H "Authorization: Bearer td_your_key"
```

### Auto-discover tools from a URL

```bash
curl -X POST https://api.toolsdns.com/v1/discover \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://smithery.ai/server/@modelcontextprotocol/server-github"}'
```

Supports: Smithery URLs, npm packages, GitHub repos, raw HTTP MCP endpoints.

### Health check (no auth)

```bash
curl https://api.toolsdns.com/health
# {"status":"healthy","tools_indexed":1709,"sources":4}
```

---

## Configuration

All settings via environment variables or `~/.tooldns/.env`:

| Variable | Default | Description |
|---|---|---|
| `TOOLDNS_API_KEY` | `td_dev_key` | Master API key (change in production!) |
| `TOOLDNS_HOST` | `0.0.0.0` | Bind address |
| `TOOLDNS_PORT` | `8787` | Server port |
| `TOOLDNS_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformer model for search |
| `TOOLDNS_REFRESH_INTERVAL` | `15` | Auto re-index interval in minutes (0 = off) |
| `TOOLDNS_MODEL` | *(auto-detect)* | LLM model name for token cost calculations |
| `TOOLDNS_LOG_LEVEL` | `INFO` | Log verbosity |
| `TOOLDNS_WEBHOOK_URL` | *(empty)* | URL to POST health alerts to |
| `TOOLDNS_WEBHOOK_SECRET` | *(empty)* | HMAC secret for webhook verification |
| `TOOLDNS_APP_NAME` | `ToolsDNS` | Brand name shown in UI (white-label) |
| `TOOLDNS_APP_TAGLINE` | `DNS for AI Tools` | Tagline shown in UI |
| `TOOLDNS_CONTACT_EMAIL` | `hello@toolsdns.com` | Support email in UI footer |
| `TOOLDNS_GITHUB_URL` | GitHub repo URL | Linked in UI footer |

---

## Deploy

### VPS (Ubuntu/Debian) — Recommended

```bash
curl -sSL https://raw.githubusercontent.com/syedfahimdev/ToolsDNS/master/deploy.sh | sudo bash
```

Then add a reverse proxy (Caddy handles HTTPS automatically):

```
# /etc/caddy/Caddyfile
api.yourdomain.com {
    reverse_proxy localhost:8787
}
```

### Separate Frontend

The web dashboard (`toolsdns-web`) can be deployed to Vercel independently:

```
Backend: api.toolsdns.com  →  your VPS running ToolsDNS
Frontend: toolsdns.com     →  Vercel (toolsdns-web repo)
```

Frontend repo: [github.com/syedfahimdev/toolsdns-web](https://github.com/syedfahimdev/toolsdns-web)

Set two env vars in Vercel:
```
TOOLDNS_API_URL = https://api.yourdomain.com
TOOLDNS_API_KEY = td_your_key_here
```

### Railway / Render / Fly.io

See the **🚀 Deploy** page in the web UI at `/ui/deploy` for platform-specific guides with all env vars pre-filled.

---

## Project Structure

```
ToolsDNS/
├── deploy.sh              # One-command VPS installer
├── main.py                # FastAPI app + network ACL middleware
├── docker-compose.yml     # Bind-mounts ~/.tooldns, exposes port 8787
├── pyproject.toml         # Package config — CLI: toolsdns / tooldns
├── tooldns/               # Main Python package
│   ├── api.py             # REST API routes (/v1/*)
│   ├── auth.py            # API key auth — admin key + named sub-keys
│   ├── categories.py      # Auto-categorization (15 categories)
│   ├── cli.py             # CLI: toolsdns setup / serve / add / search
│   ├── config.py          # Settings from env vars
│   ├── database.py        # SQLite with FTS5 full-text + embedding cache
│   ├── discover.py        # Auto-discover from Smithery/npm/GitHub/HTTP
│   ├── embedder.py        # Local sentence-transformer embeddings
│   ├── fetcher.py         # MCP protocol client (stdio + HTTP transport)
│   ├── health.py          # Source health monitor + webhook alerts
│   ├── ingestion.py       # Parallel tool indexing pipeline
│   ├── marketplace.py     # Pre-built MCP server catalog
│   ├── mcp_server.py      # FastMCP server — exposes ToolsDNS via MCP
│   ├── models.py          # Pydantic models
│   ├── search.py          # Hybrid semantic + BM25 search engine
│   ├── tokens.py          # Real token counting + per-model cost calc
│   ├── ui.py              # Web UI routes (/ui/*) — Jinja2 + HTMX
│   ├── templates/         # HTML templates (base, tools, sources, etc.)
│   └── static/            # CSS + JS
└── .github/
    ├── workflows/ci.yml           # Tests, import checks, branding lint
    └── workflows/security.yml     # Bandit, Safety CVE scan, secret detection
```

---

## Contributing

**All contributions are welcome.** ToolsDNS is built by the community for the community.

### Ways to Contribute

| Type | Examples |
|---|---|
| 🛠 **New MCP connectors** | Add a server to `marketplace.py` — Notion, Linear, Jira, etc. |
| 🎯 **Better categorization** | Improve `categories.py` rules for more accurate tool tagging |
| 🔍 **Search improvements** | Tune BM25/semantic weights, add re-ranking, test edge cases |
| 🎨 **UI/UX** | Improve templates, add dark/light polish, mobile responsiveness |
| 📖 **Skills library** | Share useful `SKILL.md` files for common workflows |
| 🐛 **Bug fixes** | Check [open issues](https://github.com/syedfahimdev/ToolsDNS/issues) |
| 📝 **Documentation** | Improve this README, add examples, write guides |
| 🌐 **Translations** | Localize the web UI for other languages |

### Getting Started

```bash
# 1. Fork the repo on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/ToolsDNS.git
cd ToolsDNS

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install in editable mode with dev dependencies
pip install -e .

# 4. Create a branch for your feature
git checkout -b feat/my-new-feature

# 5. Make changes, then test
toolsdns serve
# Open http://localhost:8787/ui and verify your changes

# 6. Push and open a PR
git push origin feat/my-new-feature
```

### Pull Request Guidelines

- **Keep PRs focused** — one feature or fix per PR
- **Test your changes** — run the server and verify the affected UI/API works
- **Don't break existing behavior** — the CI pipeline checks imports and branding
- **Add to `marketplace.py`** if adding a new MCP server — name, description, install command
- **No secrets in code** — the security workflow scans for API keys and tokens

### Adding a Marketplace Server

Open `tooldns/marketplace.py` and add an entry to the `MARKETPLACE` list:

```python
{
    "id": "notion",
    "name": "Notion",
    "description": "Read and write Notion pages, databases, and blocks",
    "category": "Productivity",
    "emoji": "📝",
    "install": {
        "type": "mcp_http",
        "url": "https://mcp.notion.com/v1",
        "env_vars": ["NOTION_API_KEY"],
    }
}
```

### Adding a Skill

Create a folder in `~/.tooldns/skills/your-skill-name/SKILL.md`:

```markdown
---
name: your-skill-name
description: What this skill does in one sentence
---

# Your Skill Name

Step-by-step instructions for the agent to follow...
```

Share useful skills by submitting them to the [skills library discussion](https://github.com/syedfahimdev/ToolsDNS/discussions).

### Code Style

- Python: follow existing patterns (no formatter enforced, just be consistent)
- Templates: Jinja2 + HTMX (see `templates/` for examples)
- No unnecessary dependencies — the core should stay lightweight

### Reporting Issues

- **Bug?** → [Open an issue](https://github.com/syedfahimdev/ToolsDNS/issues/new?template=bug_report.md) with steps to reproduce
- **Feature idea?** → [Start a discussion](https://github.com/syedfahimdev/ToolsDNS/discussions/new)
- **Security vulnerability?** → Email [syed@toolsdns.com](mailto:syed@toolsdns.com) privately

---

## License

**AGPL-3.0** for open-source use. See [LICENSE](LICENSE).

For commercial/proprietary use (closed-source products, SaaS resale, white-label), contact [syed@toolsdns.com](mailto:syed@toolsdns.com) for a commercial license.

---

<div align="center">

Built with ❤️ by [Syed Fahim](https://github.com/syedfahimdev) and contributors

[⭐ Star this repo](https://github.com/syedfahimdev/ToolsDNS) if ToolsDNS saves you tokens!

**[toolsdns.com](https://toolsdns.com)** · [hello@toolsdns.com](mailto:hello@toolsdns.com) · [Issues](https://github.com/syedfahimdev/ToolsDNS/issues)

</div>
