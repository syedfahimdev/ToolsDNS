# ToolsDNS Roadmap — Top-Notch Features

> Last updated: 2026-03-18
> Status: Phase 2 shipped — execution layer, macros, analytics, agent memory live

---

## Phase 1: Core Multi-Agent Optimizations (MVP Complete ✓)

| Feature | Status | Impact | Effort |
|---------|--------|--------|--------|
| Minimal Schema Mode | ✅ Shipped | ~70% token reduction | Low |
| Agent Sessions (Dedup) | ✅ Shipped | 100% on repeats | Low |
| Batch Search | ✅ Shipped | 16→1 HTTP calls | Medium |
| Tool Profiles | ✅ Shipped | Faster + focused | Medium |

**Shipped in commits:**
- `b316778` — Docker + E2E walkthrough
- `6e426c2` — Multi-agent token savings
- `7307eb4` — README documentation

---

## Phase 2: Agent Experience (Complete ✓)

### 2.1 Smart Tool Chaining ⭐ SHIPPED
**What:** One query → suggested multi-tool workflow with real execution
**Why:** Agents often need 3-4 tools in sequence. Saves 3-4 searches.
**Implementation:**
- [x] `workflow_patterns` table with trigger phrases, steps, parallel groups
- [x] `POST /v1/suggest-workflow` — fuzzy match + agent preference boost
- [x] `POST /v1/execute-workflow` — **real tool calls** via caller.py
- [x] `POST /v1/learn` — pattern learning from tool call sequences
- [x] Argument chaining between steps (`{step.1.field}` syntax)
- [x] Parallel execution via `asyncio.gather` for independent steps
- [x] Retry logic with configurable `retry_count` per step
- [x] Error handling: `on_error` = "stop" | "skip" | "retry"
- [x] Conditional steps (`if {variable} == value`)
- [x] Dry-run mode for previewing workflows

**Tokens saved:** 3-4x per multi-step task
**Effort:** Medium

---

### 2.2 Tool Composition (Macros) ✅ SHIPPED
**What:** Define reusable multi-tool workflows as single virtual tools
**Example:**
```python
POST /v1/macros
{
  "name": "deploy-and-notify",
  "steps": [
    {"tool_id": "GITHUB_CREATE_RELEASE", "arg_template": {"tag": "{version}"}},
    {"tool_id": "SLACK_SEND_MESSAGE", "arg_template": {"text": "Deployed {version}"}},
    {"tool_id": "TWITTER_POST", "arg_template": {"text": "v{version} is live!"}}
  ]
}

# Call with one request:
POST /v1/call
{"tool_id": "macro__deploy-and-notify", "arguments": {"version": "1.2.0"}}
```
**Implementation:**
- [x] `MacroStep` + `CreateMacroRequest` models
- [x] `POST /v1/macros` — create macro
- [x] `GET /v1/macros` — list user macros
- [x] `DELETE /v1/macros/{id}` — delete macro
- [x] `POST /v1/call` — detect `macro__` prefix, execute all steps
- [x] Argument resolution with `{placeholder}` templates
- [x] Stop on failure (respects `on_error` per step)

**Tokens saved:** 3-4x per repeated workflow
**Effort:** Medium

---

### 2.3 Tool Performance Analytics ✅ SHIPPED
**What:** Track which tools agents actually use vs just search for
**Implementation:**
- [x] Every `/v1/call` logs to `tool_call_sequences` table
- [x] `GET /v1/analytics/popular` — most-called tools by count
- [x] `GET /v1/analytics/unused` — indexed but never called (cleanup candidates)
- [x] `GET /v1/analytics/agents` — per-agent stats + favorite tools
- [x] `GET /v1/analytics/conversion` — search-to-call conversion rates
- [x] Agent preference auto-learning from `/v1/call` usage

**Tokens saved:** Faster searches over time (smaller index)
**Effort:** Medium

### 2.4 Real Tool Execution Layer ✅ SHIPPED
**What:** `/v1/call` actually executes tools, not just returns schemas
**Implementation:**
- [x] `caller.py` — shared execution module (extracted from api.py)
- [x] Supports stdio MCP, HTTP MCP, skills, and macros
- [x] `CallToolRequest` Pydantic model with `agent_id` + `query` tracking
- [x] Automatic tool selection recording for preference learning
- [x] Workflow engine wired to real execution via `tool_caller` callable
- [x] `resolve_args()` — argument templating with `{var}` and `{step.N.field}`

---

## Phase 3: Intelligence Layer (Month 2-3)

### 3.1 Agent Memory / Personalized Search
**What:** Agents learn preferences, ToolsDNS remembers  
**Why:** Personalized search = better accuracy, fewer retries  
**Example:**
```
Agent "email-bot" always picks GMAIL_SEND_EMAIL over OUTLOOK_SEND_EMAIL
→ Boost GMAIL_* results for this agent by +0.1 confidence
```
**Implementation:**
- [ ] Per-agent preference tracking (from analytics)
- [ ] Boost scores based on historical selection
- [ ] Store in session or agent profile
- [ ] `POST /v1/search` accepts `agent_id` for personalization

**Tokens saved:** Fewer searches (first result is right)  
**Effort:** Medium  
**Depends on:** 2.3 Analytics

---

### 3.2 Semantic Tool Arguments
**What:** Natural language → suggested tool arguments  
**Why:** Agents describe intent, ToolsDNS extracts structured args  
**Example:**
```
POST /v1/search
{
  "query": "send an email to john about the meeting tomorrow",
  "natural_args": true
}

→ {
  "tool": "GMAIL_SEND_EMAIL",
  "suggested_args": {
    "to": ["john@example.com"],
    "subject": "Meeting Tomorrow",
    "body": "Hi John, just confirming our meeting tomorrow..."
  }
}
```
**Implementation:**
- [ ] LLM call to extract entities from query
- [ ] Map entities to tool schema fields
- [ ] Confidence score for each suggestion
- [ ] Agent confirms before executing

**Tokens saved:** Fewer back-and-forth clarifications  
**Effort:** High (needs LLM integration)  
**Depends on:** Nothing

---

### 3.3 Live Tool Preview
**What:** See what tool will do before calling  
**Why:** Prevents expensive mistakes  
**Example:**
```
POST /v1/preview
{
  "tool_id": "GITHUB_CREATE_ISSUE",
  "arguments": {"title": "Bug", "body": "..."}
}

→ {
  "preview": "Will create issue #142 in repo syedfahimdev/ToolsDNS",
  "side_effects": ["Notification sent to 3 watchers"],
  "estimated_cost": "$0.002",
  "confirm": true
}
```
**Implementation:**
- [ ] Dry-run mode for MCP tools (where supported)
- [ ] Cost estimation from historical data
- [ ] Side-effect detection from tool descriptions
- [ ] Human-in-the-loop confirmation flow

**Tokens saved:** Prevents wasted calls  
**Effort:** Medium-High  
**Depends on:** 2.3 Analytics (for cost estimation)

---

## Phase 4: Network & Community (Month 3-4)

### 4.1 Real-Time Tool Marketplace ⭐ GROWTH
**What:** Community-contributed tools, rated by usage  
**Why:** Network effect, makes ToolsDNS the "npm of AI tools"  
**Example:**
```
GET /v1/marketplace/trending
→ [
  {"name": "linear-create-issue", "installs": 15420, "rating": 4.8},
  {"name": "notion-quick-capture", "installs": 8934, "rating": 4.9}
]

POST /v1/marketplace/install
{"tool_id": "linear-create-issue"}
→ Auto-registers MCP server, indexes tools
```
**Implementation:**
- [ ] Public registry API (separate service?)
- [ ] Rating/review system
- [ ] One-click install from marketplace
- [ ] Verified publisher badges
- [ ] Revenue share for tool creators?

**Tokens saved:** N/A (growth feature)  
**Effort:** High  
**Depends on:** Nothing

---

### 4.2 Federation (Multi-ToolsDNS)
**What:** Search across multiple ToolsDNS instances  
**Why:** Unified search across org boundaries, private tools stay private  
**Example:**
```
Your ToolsDNS → queries → Partner's ToolsDNS
            → queries → Company's internal ToolsDNS
            → queries → Public ToolsDNS

→ Unified results from all sources
```
**Implementation:**
- [ ] Federation protocol (HTTP-based)
- [ ] `POST /v1/federate` — register remote ToolsDNS
- [ ] Parallel search across federated nodes
- [ ] Result aggregation and ranking
- [ ] Access control for private tools

**Tokens saved:** N/A (scale feature)  
**Effort:** High  
**Depends on:** Nothing

---

## Quick Wins (Can Ship This Week)

| Feature | Why | Effort |
|---------|-----|--------|
| Web UI Dashboard | Visual tool browser, session manager, cost tracker | Medium |
| CLI `tooldns stats` | Terminal analytics (popular tools, cost saved) | Low |
| Webhook notifications | Alert when source goes down, daily cost report | Low |
| Auto-cleanup dead tools | Remove tools not called in 30 days | Low |

---

## Decision Matrix

| Feature | Token Impact | User Impact | Build Effort | Priority |
|---------|-------------|-------------|--------------|----------|
| Smart Tool Chaining | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Medium | **#1** |
| Tool Composition (Macros) | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Medium | **#2** |
| Analytics Dashboard | ⭐⭐ | ⭐⭐⭐⭐ | Medium | **#3** |
| Semantic Arguments | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | High | #4 |
| Agent Memory | ⭐⭐⭐ | ⭐⭐⭐⭐ | Medium | #5 |
| Tool Preview | ⭐⭐ | ⭐⭐⭐⭐ | Medium-High | #6 |
| Marketplace | - | ⭐⭐⭐⭐⭐ | High | #7 (growth) |
| Federation | - | ⭐⭐⭐⭐ | High | #8 (scale) |

---

## Recommended Build Order

**Week 1-2:** Smart Tool Chaining  
**Week 3-4:** Tool Composition (Macros)  
**Week 5-6:** Analytics Dashboard + CLI stats  
**Month 2:** Agent Memory + Semantic Arguments  
**Month 3:** Tool Preview + Web UI  
**Month 4:** Marketplace (if traction justifies it)

---

## Phase 5: Developer Experience & Ecosystem (Month 4-5)

### 5.1 VS Code Extension ⭐ DX PRIORITY
**What:** Inline tool discovery while coding agents
**Why:** Meet developers where they work — no context switching
**Features:**
- [ ] Auto-complete for tool names while typing
- [ ] Hover to see tool schema + description
- [ ] Command palette: "ToolsDNS: Search for tool"
- [ ] Status bar: token savings today
- [ ] Debug panel: view agent sessions, tool calls

**Implementation:**
- [ ] TypeScript VS Code extension
- [ ] LSP-like tool suggestion provider
- [ ] Webview panel for tool browser
- [ ] Settings sync with ToolsDNS API key

**Effort:** Medium  
**Impact:** ⭐⭐⭐⭐⭐ Developer adoption

---

### 5.2 CLI Enhancement Suite
**What:** Rich terminal experience for ToolsDNS
**Why:** Power users prefer CLI over web UI
**Features:**
- [ ] `tooldns search "send email"` — quick search from terminal
- [ ] `tooldns stats` — daily/weekly analytics
- [ ] `tooldns test <tool_id>` — test tool with dry-run
- [ ] `tooldns logs` — tail tool call logs
- [ ] `tooldns doctor` — diagnose common issues
- [ ] `tooldns export` — backup config + skills

**Implementation:**
- [ ] Rich terminal UI (rich library)
- [ ] Interactive prompts (inquirer)
- [ ] Progress bars for long operations
- [ ] JSON/YAML output modes for scripting

**Effort:** Low-Medium  
**Impact:** ⭐⭐⭐⭐ Power user satisfaction

---

### 5.3 Integration Ecosystem
**What:** Native plugins for popular frameworks
**Why:** Reduce friction for existing projects
**Integrations:**
- [ ] **LangChain** — `ToolsDNSToolKit` class
- [ ] **LlamaIndex** — tool retriever
- [ ] **n8n** — custom node for workflow automation
- [ ] **Zapier** — connector for no-code users
- [ ] **Discord Bot** — natural language tool calling in chat
- [ ] **GitHub Actions** — use tools in CI/CD pipelines

**Implementation:**
- [ ] Separate repos for each integration
- [ ] Consistent SDK pattern across all
- [ ] Documentation + examples

**Effort:** Medium (per integration)  
**Impact:** ⭐⭐⭐⭐⭐ Ecosystem growth

---

## Phase 6: Performance & Scale (Month 5-6)

### 6.1 Vector Database Migration
**What:** Replace in-memory embeddings with vector DB
**Why:** Scale to 100k+ tools without memory issues
**Options:**
- [ ] Pinecone (managed, expensive)
- [ ] Weaviate (open source, self-hosted)
- [ ] pgvector (if already using Postgres)
- [ ] Qdrant (fast, Rust-based)

**Implementation:**
- [ ] Abstract embedding storage interface
- [ ] Migration script for existing tools
- [ ] Hybrid search (vector + BM25)
- [ ] Sub-10ms search at 100k tools

**Effort:** High  
**Impact:** ⭐⭐⭐⭐⭐ Scale capability

---

### 6.2 Caching & CDN Layer
**What:** Multi-level caching for tool schemas
**Why:** Reduce API latency, handle traffic spikes
**Layers:**
- [ ] **L1:** In-memory LRU (current)
- [ ] **L2:** Redis for cross-instance sharing
- [ ] **L3:** CDN for static tool schemas (CloudFlare)
- [ ] **L4:** Browser cache for web UI

**Implementation:**
- [ ] Cache invalidation on tool updates
- [ ] Cache warming for popular tools
- [ ] Cache hit/miss analytics

**Effort:** Medium  
**Impact:** ⭐⭐⭐⭐ Performance

---

### 6.3 Connection Pooling & MCP Optimization
**What:** Efficient MCP server connection management
**Why:** Eliminate 1.3s cold-start penalty
**Features:**
- [ ] Persistent stdio process pools
- [ ] HTTP keep-alive for MCP servers
- [ ] Lazy connection (connect on first use)
- [ ] Connection health checks
- [ ] Auto-restart dead connections

**Implementation:**
- [ ] Connection pool manager
- [ ] Circuit breaker pattern for failing servers
- [ ] Connection metrics (active, idle, failed)

**Effort:** Medium  
**Impact:** ⭐⭐⭐⭐⭐ Latency reduction

---

## Phase 7: Security & Enterprise (Month 6-7)

### 7.1 Security Hardening
**What:** Production-ready security features
**Why:** Enterprise adoption requires security
**Features:**
- [ ] Rate limiting per API key (token bucket)
- [ ] IP allowlisting for sensitive tools
- [ ] Audit logging (who called what, when)
- [ ] Secret rotation automation
- [ ] Tool sandboxing (isolated containers)
- [ ] SOC 2 compliance documentation

**Implementation:**
- [ ] Middleware for rate limiting
- [ ] Audit log table with retention
- [ ] Automated security scanning (Snyk, Trivy)

**Effort:** High  
**Impact:** ⭐⭐⭐⭐⭐ Enterprise readiness

---

### 7.2 Multi-Tenant Architecture
**What:** True SaaS with isolated workspaces
**Why:** Scale to thousands of users
**Features:**
- [ ] Organization/team workspaces
- [ ] Role-based access control (RBAC)
- [ ] Resource quotas per tenant
- [ ] Tenant-specific tool indexing
- [ ] White-label options

**Implementation:**
- [ ] Tenant isolation in database
- [ ] Subdomain routing (org.toolsdns.com)
- [ ] Billing per tenant (Stripe integration)

**Effort:** High  
**Impact:** ⭐⭐⭐⭐⭐ Business model

---

## Phase 8: Intelligence & Automation (Month 7-8)

### 8.1 Auto-Workflow Discovery
**What:** AI suggests workflows from usage patterns
**Why:** Users don't know what workflows they need
**Features:**
- [ ] Analyze tool call sequences
- [ ] Suggest workflow patterns
- [ ] One-click workflow creation
- [ ] Workflow effectiveness scoring

**Implementation:**
- [ ] Sequence mining algorithm
- [ ] LLM-based workflow suggestion
- [ ] A/B testing for workflow recommendations

**Effort:** High  
**Impact:** ⭐⭐⭐⭐⭐ User value

---

### 8.2 Self-Healing System
**What:** Automatic problem detection & resolution
**Why:** Reduce operational overhead
**Features:**
- [ ] Auto-restart failed MCP servers
- [ ] Detect and quarantine broken tools
- [ ] Auto-scale based on load
- [ ] Predictive maintenance alerts
- [ ] Cost anomaly detection

**Implementation:**
- [ ] Health check automation
- [ ] Kubernetes operator (optional)
- [ ] ML-based anomaly detection

**Effort:** High  
**Impact:** ⭐⭐⭐⭐ Operational excellence

---

## Quick Wins (Can Ship This Week) — UPDATED

| Feature | Why | Effort | Priority |
|---------|-----|--------|----------|
| Web UI Dashboard | Visual tool browser, session manager, cost tracker | Medium | ⭐⭐⭐⭐⭐ |
| CLI `tooldns stats` | Terminal analytics (popular tools, cost saved) | Low | ⭐⭐⭐⭐⭐ |
| Fix arguments parameter bug | Blocking bug in tool calls | Low | ⭐⭐⭐⭐⭐ URGENT |
| Webhook notifications | Alert when source goes down, daily cost report | Low | ⭐⭐⭐⭐ |
| Auto-cleanup dead tools | Remove tools not called in 30 days | Low | ⭐⭐⭐ |
| VS Code Extension MVP | Basic tool search in editor | Medium | ⭐⭐⭐⭐⭐ |

---

## Updated Decision Matrix

| Feature | Token Impact | User Impact | Build Effort | Priority |
|---------|-------------|-------------|--------------|----------|
| **Fix arguments bug** | - | ⭐⭐⭐⭐⭐ | Low | **URGENT** |
| Smart Tool Chaining | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Medium | **#1** |
| Tool Composition (Macros) | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Medium | **#2** |
| VS Code Extension | - | ⭐⭐⭐⭐⭐ | Medium | **#3** |
| Vector DB Migration | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | High | #4 |
| Analytics Dashboard | ⭐⭐ | ⭐⭐⭐⭐ | Medium | #5 |
| Security Hardening | - | ⭐⭐⭐⭐⭐ | High | #6 |
| Multi-Tenant | - | ⭐⭐⭐⭐⭐ | High | #7 |
| Marketplace | - | ⭐⭐⭐⭐⭐ | High | #8 |

---

## Notes

- All features backward compatible
- Each feature includes token savings tracking
- Focus on "ship fast, measure impact" — your vibe coding style
- After each phase, update this PLAN.md with actual metrics
- **CRITICAL:** Fix the arguments parameter bug before shipping new features

---

**Next step:** 
1. **URGENT:** Fix the `arguments` parameter bug in tool calls
2. Then pick: VS Code Extension (DX) or Vector DB (Scale)
