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

## Notes

- All features backward compatible
- Each feature includes token savings tracking
- Focus on "ship fast, measure impact" — your vibe coding style
- After each phase, update this PLAN.md with actual metrics

---

**Next step:** Pick Phase 2.1 (Smart Tool Chaining) or 2.2 (Tool Composition) and I'll start building.
