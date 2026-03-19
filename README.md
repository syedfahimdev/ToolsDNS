<div align="center">

# ToolsDNS

**DNS for AI Tools** — Semantic search over thousands of MCP tools. Return only what the agent needs.

[![CI](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/ci.yml/badge.svg)](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/ci.yml)
[![Security](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/security.yml/badge.svg)](https://github.com/syedfahimdev/ToolsDNS/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
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

> **Real numbers from production:** searching 5,000+ tools returns ~3 results, saving **284,000+ tokens per search** — roughly **$0.85 per query** at Claude Sonnet pricing.

---

## What ToolsDNS Does

```
┌─────────────────┐     ┌──────────────────────┐     ┌──────────────┐
│  Your Tools      │     │      ToolsDNS          │     │  AI Agent    │
│                  │     │                       │     │              │
│ • MCP Servers    │────▶│  Index + Embed        │     │ "What can    │
│ • Skill files    │     │  5,000+ tool schemas  │◀────│  you do?"    │
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
| 🔍 **Semantic Search** | Natural language queries — "send email" finds `GMAIL_SEND_EMAIL` at 0.81 confidence |
| ⚡ **Hybrid Scoring** | Semantic similarity (70%) + BM25 keyword (30%) + `match_reason` explains every result |
| 🧠 **BGE-base Embeddings** | 768-dimensional `bge-base-en-v1.5` model with instruction-prefixed queries for top-tier retrieval |
| 🎯 **Preflight Endpoint** | `POST /v1/preflight` — one call does intent extraction + multi-strategy search. Framework-agnostic |
| 🔀 **Multi-Intent Splitting** | Compound messages ("check calendar AND send email AND search reddit") split into sub-queries automatically |
| 🚀 **Persistent MCP Server** | Runs as a systemd service on port 8788 — 11ms connect vs 1.3s cold-start spawn |
| ⚡ **Query Cache** | Thread-safe LRU cache (256 entries, 60s TTL) — repeat searches return in ~17ms |
| 🛡️ **Duplicate Call Guard** | 30-second dedup window — prevents agents from calling the same tool twice |
| 📋 **Skill Listing** | `list_skills()` MCP tool — agents instantly discover all your custom skills |
| 🤖 **System Prompt Generator** | `GET /v1/system-prompt` or `tooldns system-prompt` — ready-to-paste agent onboarding |
| 📎 **File Download Endpoint** | `GET /dl/{token}` — skills return download URLs instead of raw base64, preventing 400 errors |
| 📊 **Token Savings Tracker** | Real token counting with per-model cost savings (not estimates) |
| 🏥 **Health Monitoring** | Auto-checks if MCP servers are online/degraded/down — webhooks on status change |
| 🛒 **Marketplace** | One-click install for GitHub, Slack, Gmail, Notion, and 30+ popular MCP servers |
| 🔌 **MCP Protocol** | Exposes itself as an MCP server — plug into Claude Desktop, Cursor, Cline, any agent |
| 📦 **Skill Management** | Create, read, update skill files via API. Drop `.xlsx`/`.pdf` files alongside `SKILL.md` |
| 🔧 **Auto-Discovery** | Point at any Smithery URL, npm package, GitHub repo, or HTTP MCP endpoint |
| 🔑 **API Key Manager** | Multi-tenant sub-keys with per-key usage tracking and monthly limits |
| 🚀 **One-Command Deploy** | `curl \| bash` installer for any Ubuntu/Debian VPS |
| 🔄 **Hot Reload** | Edit `config.json` or skills → tools re-index automatically, no restart needed |
| 🔄 **Skills Watcher** | File watcher on `~/.tooldns/skills/` — new or changed skills re-index instantly |
| 🛡️ **Resilient Ingestion** | Auto-retry with exponential backoff (3 attempts), per-tool fallback on embedding failure |
| 🏷️ **Tool Categories** | 15 categories auto-assigned (Dev & Code, Communication, AI & Agents, etc.) |
| 🔒 **Security First** | Bandit static analysis + CVE scanning in CI, API key auth on all endpoints |
| 🎯 **Tool Profiles** | Scope agents to relevant tool subsets — "email-agent" only sees 20 tools, not 5,000 |
| 🧠 **Agent Sessions** | Schema dedup per session — never send the same tool schema twice to the same agent |
| 📦 **Batch Search** | Multiple queries in one HTTP call — 16 agents → 1 request with cross-query dedup |
| ✂️ **Minimal Mode** | Strip schemas to required fields only — ~70% token reduction per result |

---

## Quick Start

### Option 1 — One-Command Install (VPS / Linux)

```bash
curl -sSL https://raw.githubusercontent.com/syedfahimdev/ToolsDNS/master/deploy.sh | sudo bash
```

Installs Python env, configures **two** systemd services (`tooldns` API + `tooldns-mcp` persistent MCP server), generates API key, and verifies health. Done in ~60 seconds.

### Option 2 — Local Dev

```bash
git clone https://github.com/syedfahimdev/ToolsDNS.git
cd ToolsDNS
pip install -e .
toolsdns serve
```

API is now running at **http://localhost:8787** and the MCP server at **http://127.0.0.1:8788/mcp/**.

### Option 3 — Docker

```bash
git clone https://github.com/syedfahimdev/ToolsDNS.git
cd ToolsDNS
cp .env.example .env          # set TOOLDNS_API_KEY
docker compose up -d
```

- REST API: **http://localhost:8787**
- MCP Server: **http://localhost:8788/mcp**
- Your `~/.tooldns/` folder is bind-mounted — config, skills, and database all persist on the host.

Check it's running:

```bash
curl http://localhost:8787/health
# {"status":"healthy","tools_indexed":0,"sources":0}
```

---

## First-Time Agent Setup

After installing, generate a system prompt for your AI agent in one command:

```bash
tooldns system-prompt
```

Or via the API:

```bash
curl https://api.toolsdns.com/v1/system-prompt \
  -H "Authorization: Bearer td_your_key"
```

This outputs a complete, ready-to-paste block explaining ToolsDNS to your agent — including live tool count, all your skills, and usage rules. Paste it into your agent's system prompt and it will immediately know how to use all your tools.

**Example output:**
```
## ToolsDNS — Tool Discovery Layer

You have access to 5,056 tools indexed in ToolsDNS...

### Available Skills
- **cea-weekly-report**: Fill the weekly Excel report...
- **everi-work-order**: Create Excel work orders for parts and returns...
...
```

---

## MCP Integration

### Persistent HTTP MCP Server (recommended)

ToolsDNS runs a dedicated MCP HTTP server on port **8788** as a background service. Connecting via URL eliminates the ~1.3s cold-start of spawning a new Python process per session.

**Claude Desktop / Cursor / Cline / any MCP client:**

```json
{
  "mcpServers": {
    "tooldns": {
      "type": "streamable-http",
      "url": "https://api.yourdomain.com/mcp",
      "headers": { "Authorization": "Bearer td_your_key" }
    }
  }
}
```

**copaw / agentscope / older MCP clients:**

ToolsDNS automatically handles clients that don't send `Accept: application/json, text/event-stream` — the server injects the required headers server-side. Both `/mcp` and `/mcp/` are accepted (no 307 redirect).

### Tools your agent gets

| MCP Tool | When to call it |
|---|---|
| `list_skills()` | User asks "what can you do?" or "what skills do you have?" |
| `search_tools(query)` | Before using any tool — find what's available |
| `get_tool(id)` | Need full schema for a specific tool |
| `call_tool(id, args)` | Execute a tool through ToolsDNS |
| `read_skill(name)` | Get full SKILL.md instructions before running a skill |
| `get_system_prompt()` | Get the system prompt to onboard a new agent |
| `create_skill(...)` | Create a new skill file |
| `register_mcp_server(...)` | Add a new MCP server to the index |

**Example agent workflow:**

```
User: "Create a work order for the maintenance team"

Agent → list_skills()                    # What skills exist?
      → sees "everi-work-order" listed
      → read_skill("everi-work-order")   # Get full instructions
      → follows SKILL.md instructions
      → creates the Excel work order ✓
      → work_order_get_file()            # Returns download URL, not base64
      → sends file to user ✓
```

---

## Execute Any Tool — End-to-End Walkthrough

> This is the core use case: an AI agent executes **any** of your 5,000+ tools using nothing but natural language.

### Step 1 — Register a tool source

Point ToolsDNS at a Composio MCP server (or any MCP server):

```bash
curl -X POST http://localhost:8787/v1/sources \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "mcp_http",
    "name": "composio",
    "url": "https://mcp.composio.dev/composio/mcp?apiKey=YOUR_COMPOSIO_KEY"
  }'
# {"name":"composio","tools_count":2516,"status":"active"}
```

Tools are indexed and searchable immediately — no restart needed.

---

### Step 2 — Search for the right tool

```bash
curl -X POST http://localhost:8787/v1/search \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "send an email via gmail", "top_k": 1}'
```

```json
{
  "results": [{
    "id": "composio__GMAIL_SEND_EMAIL",
    "name": "GMAIL_SEND_EMAIL",
    "description": "Sends an email via Gmail API using the authenticated user's Google profile",
    "confidence": 0.94,
    "match_reason": "strong semantic match (0.94); keyword match (BM25 0.87)",
    "input_schema": { "to": "string", "subject": "string", "body": "string" }
  }],
  "total_tools_indexed": 5056,
  "tokens_saved": 284710,
  "search_time_ms": 17.4
}
```

~284,000 tokens saved vs loading all 5,000 schemas into context.

---

### Step 3 — Execute the tool

Pass the `id` from Step 2 directly to `/v1/call`:

```bash
curl -X POST http://localhost:8787/v1/call \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_id": "composio__GMAIL_SEND_EMAIL",
    "arguments": {
      "to": ["alice@example.com"],
      "subject": "Hello from ToolsDNS",
      "body": "This email was sent by an AI agent via ToolsDNS."
    }
  }'
# {"type":"mcp_result","result":{"status":"sent","messageId":"..."}}
```

ToolsDNS proxies the call to the original MCP server and returns the result. The agent never needs to know which server the tool lives on.

---

### Full agent loop (Python example)

```python
import httpx

BASE = "http://localhost:8787"
HEADERS = {"Authorization": "Bearer td_your_key"}

client = httpx.Client(base_url=BASE, headers=HEADERS)

# 1. Search
results = client.post("/v1/search", json={"query": "create a GitHub issue", "top_k": 1}).json()
tool = results["results"][0]
print(f"Found: {tool['name']} (confidence: {tool['confidence']:.0%})")

# 2. Execute
result = client.post("/v1/call", json={
    "tool_id": tool["id"],
    "arguments": {
        "owner": "myorg",
        "repo": "myrepo",
        "title": "Bug: login fails on Safari",
        "body": "Steps to reproduce..."
    }
}).json()
print(result)
```

---

### Via MCP (agent-native — no HTTP calls needed)

When your agent is connected to the MCP server at port 8788, it does this automatically:

```
User: "Send a Slack message to #alerts that the deploy succeeded"

Agent → search_tools("send slack message")
      → gets: SLACK_SEND_MESSAGE (confidence 91%)
      → call_tool("composio__SLACK_SEND_MESSAGE", {
            "channel": "#alerts",
            "text": "✅ Deploy succeeded"
        })
      → "Message sent."
```

No hardcoded tool names. No 500-tool schemas in context. Just works.

---

## Preflight — One-Call Tool Discovery

The `/v1/preflight` endpoint does everything in a single HTTP call: extracts intents from the user's message, splits compound queries, runs multi-strategy search, and returns a ready-to-inject context block. Works with **any** agent framework — no framework-specific code needed.

```bash
curl -X POST http://localhost:8787/v1/preflight \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Check my calendar for next week and send the summary to fahim via email",
    "top_k": 5,
    "include_schemas": true,
    "include_call_templates": true,
    "format": "context_block"
  }'
```

**What happens server-side:**
1. Splits compound message into sub-clauses: `["Check my calendar for next week", "send the summary to fahim via email"]`
2. Extracts intent keywords via regex patterns: `["GOOGLECALENDAR_FIND_EVENT", "GMAIL_SEND_EMAIL"]`
3. Searches each clause + intent independently
4. Merges results, deduplicates, returns top matches with schemas + call templates

**Response (structured format):**
```json
{
  "found": true,
  "tools": [
    {"tool_id": "composio__GOOGLECALENDAR_FIND_EVENT", "confidence": 0.88, "call_template": "..."},
    {"tool_id": "composio__GMAIL_SEND_EMAIL", "confidence": 0.81, "call_template": "..."}
  ],
  "queries_used": ["Check my calendar for next week", "send the summary...", "GOOGLECALENDAR_FIND_EVENT", "GMAIL_SEND_EMAIL"],
  "search_time_ms": 45.2
}
```

**Response (context_block format — inject directly into LLM prompt):**
```
[ToolsDNS Auto-Discovery] — tools already found, DO NOT search again.

>>> CALL THIS NOW: toolsdns(action="call", tool_id="composio__GOOGLECALENDAR_FIND_EVENT", arguments={...})
    (tool: GOOGLECALENDAR_FIND_EVENT — Finds events in a Google Calendar)

>>> CALL THIS NOW: toolsdns(action="call", tool_id="composio__GMAIL_SEND_EMAIL", arguments={...})
    (tool: GMAIL_SEND_EMAIL — Sends an email via Gmail)
```

### Integration Example (Python)

```python
import httpx

# Call preflight BEFORE the LLM loop
resp = httpx.post("http://localhost:8787/v1/preflight", headers=HEADERS, json={
    "message": user_message,
    "format": "context_block",
    "include_schemas": True,
})
context = resp.json().get("context_block", "")

# Inject into system prompt or first user message
messages = [
    {"role": "system", "content": f"You are an AI assistant.\n\n{context}"},
    {"role": "user", "content": user_message},
]
# LLM now knows exactly which tools to call — no search step needed
```

---

## Multi-Agent Token Savings

Running 16 agents? ToolsDNS has 4 features designed specifically to minimize token costs at scale:

### 1. Minimal Schema Mode (~70% token reduction)

Strip optional fields from tool schemas — only required fields are returned:

```bash
curl -X POST http://localhost:8787/v1/search \
  -H "Authorization: Bearer td_your_key" \
  -d '{"query": "send email", "top_k": 2, "minimal": true}'
```

| Mode | Typical Schema Size | Tokens |
|------|-------------------|--------|
| Full | 6 fields (required + optional) | ~180 |
| **Minimal** | 2 fields (required only) | **~50** |

The response includes `"_minimal": true` so agents know the schema is trimmed.

---

### 2. Agent Sessions (Schema Dedup)

Create a session for each agent. ToolsDNS tracks which schemas have been sent and never resends them:

```bash
# Create a session
curl -X POST http://localhost:8787/v1/sessions \
  -H "Authorization: Bearer td_your_key" \
  -d '{"agent_id": "email-agent-1", "ttl_seconds": 3600}'
# → {"session_id": "sess_abc123", ...}

# Use session in searches
curl -X POST http://localhost:8787/v1/search \
  -H "Authorization: Bearer td_your_key" \
  -d '{
    "query": "send email",
    "session_id": "sess_abc123"
  }'
```

**Second search for the same tool:**
```json
{
  "results": [{
    "id": "composio__GMAIL_SEND_EMAIL",
    "name": "GMAIL_SEND_EMAIL",
    "description": "[already seen — use cached schema] Sends an email...",
    "input_schema": {},
    "already_seen": true
  }],
  "tokens_saved_by_dedup": 150
}
```

Sessions can be **shared across agents** — set `shared: true` and distribute the `session_id` to agents working on the same task.

---

### 3. Batch Search (16 Agents → 1 HTTP Call)

Instead of 16 separate HTTP requests, batch all queries into one:

```bash
curl -X POST http://localhost:8787/v1/search/batch \
  -H "Authorization: Bearer td_your_key" \
  -d '{
    "queries": [
      {"query": "send gmail email", "top_k": 1},
      {"query": "create github issue", "top_k": 1},
      {"query": "upload to google drive", "top_k": 1}
    ],
    "minimal": true,
    "session_id": "shared_session_abc"
  }'
```

**Benefits:**
- Single HTTP round-trip regardless of query count
- Shared `session_id` enables **cross-query dedup** — if query 1 and query 4 both match `GMAIL_SEND_EMAIL`, it's only returned once with full schema
- Combined token savings reported for the entire batch

---

### 4. Tool Profiles (Scoped Tool Subsets)

Agents shouldn't search 5,000 tools when they only need 20. Create profiles that scope searches to relevant tools:

```bash
# Create a profile for email agents
curl -X POST http://localhost:8787/v1/profiles \
  -H "Authorization: Bearer td_your_key" \
  -d '{
    "name": "email-agent",
    "description": "Agent for email and calendar tasks",
    "tool_patterns": ["GMAIL_*", "OUTLOOK_*", "GOOGLECALENDAR_*"],
    "pinned_tool_ids": ["composio__SLACK_SEND_MESSAGE"]
  }'
```

**Use the profile in searches:**
```bash
curl -X POST http://localhost:8787/v1/search \
  -H "Authorization: Bearer td_your_key" \
  -d '{
    "query": "send message",
    "profile": "email-agent"
  }'
```

**Three wins simultaneously:**
1. **Faster search** — smaller embedding matrix
2. **Better accuracy** — no noise from irrelevant tools
3. **Lower tokens** — fewer tools to return

**Example profiles:**
| Profile | Patterns | ~Tool Count |
|---------|----------|-------------|
| `email-agent` | `GMAIL_*`, `OUTLOOK_*` | ~20 |
| `code-agent` | `GITHUB_*`, `GITLAB_*`, `LINEAR_*` | ~150 |
| `data-agent` | `AIRTABLE_*`, `NOTION_*`, `GOOGLEDRIVE_*` | ~80 |
| `social-agent` | `TWITTER_*`, `LINKEDIN_*`, `DISCORDBOT_*` | ~40 |

---

### Cost Report

Track your actual savings across all agents:

```bash
curl http://localhost:8787/v1/cost-report \
  -H "Authorization: Bearer td_your_key"
```

```json
{
  "lifetime": {
    "total_searches": 15234,
    "tokens_saved_by_search": 4321092,
    "tokens_saved_by_dedup": 892341,
    "total_tokens_saved": 5213433,
    "cost_saved_usd": 15.64,
    "model": "claude-sonnet-4-6"
  },
  "cache": {"hit_rate": 0.73},
  "active_sessions": 16,
  "active_profiles": ["email-agent", "code-agent", "data-agent"]
}
```

---

## Skills

Skills are custom workflows defined as a `SKILL.md` file (+ optional `tools.py`) in `~/.tooldns/skills/your-skill-name/`.

### Creating a skill

```bash
tooldns new-skill
```

Or drop a folder manually:

```
~/.tooldns/skills/
└── my-workflow/
    ├── SKILL.md        ← required: frontmatter + agent instructions
    ├── tools.py        ← optional: Python tool functions called via bash
    └── template.xlsx   ← optional: any supporting files (xlsx, pdf, etc.)
```

**SKILL.md format:**

```markdown
---
name: my-workflow
description: "One sentence — what this skill does and when to use it"
user-invocable: true
---

# My Workflow

Step-by-step instructions for the agent...
```

### File handling in skills

Skills can read/write local files (`.xlsx`, `.pdf`, etc.) alongside `SKILL.md`. The pattern used by built-in skills:

1. Template file (`Work_Order_Form.xlsx`, `CEA Weekly Report.xlsx`) stays in the skill folder — **never modified**
2. `generate` tool does `shutil.copy2(template, dated_copy)` then edits only the copy
3. `get_file` tool reads the copy → returns a **download URL** (not base64) → deletes the copy

This prevents large base64 blobs from entering the LLM context and causing `400 BadRequest` errors from Anthropic/OpenAI.

### Re-indexing after changes

Skills are **automatically re-indexed** when files change — a file watcher monitors `~/.tooldns/skills/` for new, modified, or deleted `.py`, `.md`, `.yaml`, and `.json` files.

To manually trigger a re-index:

```bash
tooldns ingest
# or for a specific skill only:
python3 -m tooldns.cli ingest --skill my-workflow
```

---

## API Reference

All endpoints require `Authorization: Bearer <your_api_key>` (except `/health` and `/dl/{token}`).

### Generate agent system prompt

```bash
curl https://api.toolsdns.com/v1/system-prompt \
  -H "Authorization: Bearer td_your_key"
```

Returns a ready-to-paste system prompt block for your AI agent.

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
    "match_reason": "strong semantic match (0.94); keyword match (BM25 0.87)",
    "category": "Dev & Code",
    "how_to_call": {
      "type": "mcp",
      "server": "composio",
      "instruction": "Call this tool via the 'composio' MCP server."
    }
  }],
  "total_tools_indexed": 5056,
  "tokens_saved": 284710,
  "search_time_ms": 17.4
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

### Download a generated file (no auth required)

```bash
curl https://api.toolsdns.com/dl/{token} --output report.xlsx
```

Tokens are issued by skill tools (e.g. `work_order_get_file`, `cea_report_get_file`) and expire after 15 minutes.

### Health check (no auth)

```bash
curl https://api.toolsdns.com/health
# {"status":"healthy","tools_indexed":5056,"sources":4}
```

### Batch search (multi-agent)

```bash
curl -X POST https://api.toolsdns.com/v1/search/batch \
  -H "Authorization: Bearer td_your_key" \
  -H "Content-Type: application/json" \
  -d '{
    "queries": [
      {"query": "send email", "top_k": 1},
      {"query": "create github issue", "top_k": 1}
    ],
    "minimal": true,
    "session_id": "sess_abc123"
  }'
```

### Create agent session

```bash
curl -X POST https://api.toolsdns.com/v1/sessions \
  -H "Authorization: Bearer td_your_key" \
  -d '{"agent_id": "email-agent-1", "ttl_seconds": 3600}'
# → {"session_id": "sess_abc123", "expires_at": "..."}
```

### Create tool profile

```bash
curl -X POST https://api.toolsdns.com/v1/profiles \
  -H "Authorization: Bearer td_your_key" \
  -d '{
    "name": "email-agent",
    "tool_patterns": ["GMAIL_*", "OUTLOOK_*"]
  }'
```

### Cost report

```bash
curl https://api.toolsdns.com/v1/cost-report \
  -H "Authorization: Bearer td_your_key"
```

---

## Configuration

All settings via environment variables or `~/.tooldns/.env`:

| Variable | Default | Description |
|---|---|---|
| `TOOLDNS_API_KEY` | `td_dev_key` | Master API key (change in production!) |
| `TOOLDNS_HOST` | `0.0.0.0` | Bind address |
| `TOOLDNS_PORT` | `8787` | API server port |
| `TOOLDNS_PUBLIC_URL` | *(empty)* | Public base URL (e.g. `https://api.yourdomain.com`) — used in download URLs |
| `TOOLDNS_MCP_TRANSPORT` | `http` | MCP server transport: `http` or `stdio` |
| `TOOLDNS_MCP_HOST` | `127.0.0.1` | MCP server bind address |
| `TOOLDNS_MCP_PORT` | `8788` | MCP server port |
| `TOOLDNS_EMBEDDING_MODEL` | `bge-base-en-v1.5` | Embedding model for search (`bge-base-en-v1.5`, `all-MiniLM-L6-v2`, or `ollama/<model>`) |
| `TOOLDNS_REFRESH_INTERVAL` | `15` | Auto re-index interval in minutes (0 = off) |
| `TOOLDNS_MODEL` | *(auto-detect)* | LLM model name for token cost calculations |
| `TOOLDNS_LOG_LEVEL` | `INFO` | Log verbosity |
| `TOOLDNS_WEBHOOK_URL` | *(empty)* | URL to POST health alerts to |
| `TOOLDNS_WEBHOOK_SECRET` | *(empty)* | HMAC secret for webhook verification |
| `TOOLDNS_APP_NAME` | `ToolsDNS` | Brand name (white-label) |
| `TOOLDNS_APP_TAGLINE` | `DNS for AI Tools` | Tagline (white-label) |

---

## Deploy

### VPS (Ubuntu/Debian) — Recommended

```bash
curl -sSL https://raw.githubusercontent.com/syedfahimdev/ToolsDNS/master/deploy.sh | sudo bash
```

Installs and enables **two** systemd services:
- `tooldns.service` — REST API on port 8787
- `tooldns-mcp.service` — Persistent MCP HTTP server on port 8788

Then add a reverse proxy (Caddy handles HTTPS automatically):

```
# /etc/caddy/Caddyfile
api.yourdomain.com {
    reverse_proxy localhost:8787
}
```

### Railway / Render / Fly.io

Set these env vars in your platform:
```
TOOLDNS_API_KEY=td_your_strong_key
TOOLDNS_PUBLIC_URL=https://your-app.railway.app
```

Then deploy from this repo. The `toolsdns serve` command starts the API server.

---

## Project Structure

```
ToolsDNS/
├── deploy.sh              # One-command VPS installer (creates both systemd services)
├── tooldns.sh             # Management CLI (status, ingest, mcp-status, update)
├── main.py                # FastAPI app + network ACL + /dl/{token} download endpoint
├── Dockerfile             # Container image — pre-bakes embedding model
├── docker-compose.yml     # Two services: REST API (8787) + MCP server (8788)
├── .env.example           # All supported env vars with descriptions
├── pyproject.toml         # Package config — CLI: toolsdns / tooldns
├── tooldns/               # Main Python package
│   ├── api.py             # REST API routes (/v1/*)
│   ├── auth.py            # API key auth — admin key + named sub-keys
│   ├── categories.py      # Auto-categorization (15 categories)
│   ├── cli.py             # CLI: toolsdns setup / serve / system-prompt / ...
│   ├── config.py          # Settings from env vars
│   ├── database.py        # SQLite with FTS5 full-text + embedding cache
│   ├── discover.py        # Auto-discover from Smithery/npm/GitHub/HTTP
│   ├── embedder.py        # Local embeddings (BGE/ONNX/sentence-transformers/Ollama)
│   ├── fetcher.py         # MCP protocol client (stdio + HTTP transport)
│   ├── health.py          # Source health monitor + webhook alerts
│   ├── ingestion.py       # Parallel tool indexing pipeline
│   ├── marketplace.py     # Pre-built MCP server catalog
│   ├── mcp_server.py      # FastMCP server — persistent HTTP on port 8788
│   ├── models.py          # Pydantic models
│   ├── search.py          # Hybrid semantic + BM25 + LRU query cache
│   └── tokens.py          # Real token counting + per-model cost calc
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
| 📖 **Skills library** | Share useful `SKILL.md` files for common workflows |
| 🐛 **Bug fixes** | Check [open issues](https://github.com/syedfahimdev/ToolsDNS/issues) |
| 📝 **Documentation** | Improve this README, add examples, write guides |
| ⚡ **Performance** | Faster embeddings, better caching, lower memory usage |

### Getting Started

```bash
# 1. Fork the repo on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/ToolsDNS.git
cd ToolsDNS

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install in editable mode
pip install -e .

# 4. Create a branch off develop for your feature
git checkout develop
git checkout -b feat/my-new-feature

# 5. Make changes, then test
toolsdns serve
curl http://localhost:8787/health  # verify it's running

# 6. Push and open a PR against develop
git push origin feat/my-new-feature
```

> **PRs should target the `develop` branch**, not `master`.

### Pull Request Guidelines

- **Keep PRs focused** — one feature or fix per PR
- **Test your changes** — run the server and verify the affected API/behavior works
- **Don't break existing behavior** — the CI pipeline checks imports and branding
- **Add to `marketplace.py`** if adding a new MCP server — name, description, install command
- **No secrets in code** — the security workflow scans for API keys and tokens
- **Update README.md** — document new features, env vars, or API endpoints

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
- No unnecessary dependencies — the core should stay lightweight

### Reporting Issues

- **Bug?** → [Open an issue](https://github.com/syedfahimdev/ToolsDNS/issues/new?template=bug_report.md) with steps to reproduce
- **Feature idea?** → [Start a discussion](https://github.com/syedfahimdev/ToolsDNS/discussions/new)
- **Security vulnerability?** → See [SECURITY.md](SECURITY.md)

---

## License

MIT License. See [LICENSE](LICENSE).

---

<div align="center">

Built with ❤️ by [Syed Fahim](https://github.com/syedfahimdev) and contributors

[⭐ Star this repo](https://github.com/syedfahimdev/ToolsDNS) if ToolsDNS saves you tokens!

**[toolsdns.com](https://toolsdns.com)** · [Issues](https://github.com/syedfahimdev/ToolsDNS/issues) · [Discussions](https://github.com/syedfahimdev/ToolsDNS/discussions)

</div>
