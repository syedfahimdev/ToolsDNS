"""
marketplace.py — Curated catalog of popular MCP servers and skills.

Used by the web UI marketplace page to let users browse and one-click
install popular MCP servers and pre-built skills into ToolDNS.
"""

# Categories for filtering
CATEGORIES = ["All", "Dev", "Browser", "Communication", "Search", "Data", "Cloud", "AI", "Productivity", "Skills"]

# Curated MCP servers
MCP_SERVERS = [
    # ─── Dev ─────────────────────────────────────────────────────────────────
    {
        "id": "github",
        "name": "GitHub",
        "description": "Create issues, PRs, review code, search repositories, manage GitHub. Official server by GitHub.",
        "category": "Dev",
        "icon": "🐙",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@github/github-mcp-server"],
        "env_vars": {"GITHUB_PERSONAL_ACCESS_TOKEN": ""},
        "install_note": "Create a token at github.com/settings/tokens (needs repo + read:org scopes)",
        "package": "@github/github-mcp-server",
        "popular": True,
    },
    {
        "id": "git",
        "name": "Git",
        "description": "Read, search, and manipulate Git repositories. Diff, log, blame, branch management. No API key needed.",
        "category": "Dev",
        "icon": "🌿",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-git", "--repository", "/path/to/repo"],
        "env_vars": {},
        "install_note": "Change /path/to/repo to your Git repository path. Requires uvx (pip install uv).",
        "package": "mcp-server-git",
        "popular": True,
    },
    {
        "id": "gitlab",
        "name": "GitLab",
        "description": "Manage GitLab projects, merge requests, issues, and CI/CD pipelines.",
        "category": "Dev",
        "icon": "🦊",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@zereight/mcp-gitlab"],
        "env_vars": {"GITLAB_PERSONAL_ACCESS_TOKEN": "", "GITLAB_API_URL": "https://gitlab.com"},
        "install_note": "Create a token at gitlab.com/-/user_settings/personal_access_tokens",
        "package": "@zereight/mcp-gitlab",
        "popular": False,
    },
    {
        "id": "linear",
        "name": "Linear",
        "description": "Create and manage Linear issues, projects, and engineering workflows.",
        "category": "Dev",
        "icon": "📐",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@linear/mcp-server"],
        "env_vars": {"LINEAR_API_KEY": ""},
        "install_note": "Get API key at linear.app/settings/api",
        "package": "@linear/mcp-server",
        "popular": True,
    },
    {
        "id": "sentry",
        "name": "Sentry",
        "description": "Query Sentry errors, investigate issues, and resolve incidents.",
        "category": "Dev",
        "icon": "🔭",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@sentry/mcp-server"],
        "env_vars": {"SENTRY_AUTH_TOKEN": "", "SENTRY_ORG": ""},
        "install_note": "Create a token at sentry.io/settings/auth-tokens",
        "package": "@sentry/mcp-server",
        "popular": False,
    },
    {
        "id": "e2b",
        "name": "E2B Code Sandbox",
        "description": "Execute code securely in isolated cloud sandboxes. Supports Python, JS, and more.",
        "category": "Dev",
        "icon": "⚡",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@e2b/mcp-server"],
        "env_vars": {"E2B_API_KEY": ""},
        "install_note": "Get API key at e2b.dev (free tier available)",
        "package": "@e2b/mcp-server",
        "popular": True,
    },
    {
        "id": "everything",
        "name": "Everything (Test Server)",
        "description": "Official reference/test MCP server with prompts, resources, and tools. Great for testing.",
        "category": "Dev",
        "icon": "🧪",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
        "env_vars": {},
        "install_note": "No configuration needed. Official MCP reference implementation.",
        "package": "@modelcontextprotocol/server-everything",
        "popular": False,
    },
    # ─── Browser ─────────────────────────────────────────────────────────────
    {
        "id": "playwright",
        "name": "Playwright",
        "description": "Browser automation — navigate, click, fill forms, screenshot, scrape web pages.",
        "category": "Browser",
        "icon": "🎭",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@playwright/mcp"],
        "env_vars": {},
        "install_note": "No API key needed. Installs Playwright browser automatically on first run.",
        "package": "@playwright/mcp",
        "popular": True,
    },
    {
        "id": "puppeteer",
        "name": "Puppeteer",
        "description": "Headless Chrome automation — scrape, screenshot, PDF generation, web interaction.",
        "category": "Browser",
        "icon": "🤖",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "env_vars": {},
        "install_note": "No API key needed. Downloads Chromium automatically.",
        "package": "@modelcontextprotocol/server-puppeteer",
        "popular": True,
    },
    # ─── Communication ───────────────────────────────────────────────────────
    {
        "id": "slack",
        "name": "Slack",
        "description": "Send messages, read channels, search Slack workspace content.",
        "category": "Communication",
        "icon": "💬",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env_vars": {"SLACK_BOT_TOKEN": "", "SLACK_TEAM_ID": ""},
        "install_note": "Create a Slack app at api.slack.com/apps and get the Bot Token",
        "package": "@modelcontextprotocol/server-slack",
        "popular": True,
    },
    {
        "id": "notion",
        "name": "Notion",
        "description": "Read and write Notion pages, databases, and blocks.",
        "category": "Communication",
        "icon": "📓",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env_vars": {"OPENAPI_MCP_HEADERS": "Authorization: Bearer YOUR_NOTION_KEY"},
        "install_note": "Create an integration at notion.so/my-integrations",
        "package": "@notionhq/notion-mcp-server",
        "popular": True,
    },
    {
        "id": "gmail",
        "name": "Gmail",
        "description": "Read, send, and manage Gmail emails via Google APIs.",
        "category": "Communication",
        "icon": "📧",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
        "env_vars": {},
        "install_note": "Requires Google OAuth — follow setup guide after install",
        "package": "@gongrzhe/server-gmail-autoauth-mcp",
        "popular": False,
    },
    # ─── Search ──────────────────────────────────────────────────────────────
    {
        "id": "brave-search",
        "name": "Brave Search",
        "description": "Real-time web search via Brave Search API. Privacy-focused.",
        "category": "Search",
        "icon": "🦁",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env_vars": {"BRAVE_API_KEY": ""},
        "install_note": "Get API key at api.search.brave.com (free tier available)",
        "package": "@modelcontextprotocol/server-brave-search",
        "popular": True,
    },
    {
        "id": "tavily",
        "name": "Tavily",
        "description": "AI-optimized web search and content extraction for research tasks.",
        "category": "Search",
        "icon": "🔍",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "tavily-mcp"],
        "env_vars": {"TAVILY_API_KEY": ""},
        "install_note": "Get API key at app.tavily.com (free tier: 1000 searches/month)",
        "package": "tavily-mcp",
        "popular": True,
    },
    {
        "id": "exa",
        "name": "Exa",
        "description": "AI-powered web search optimized for research — finds exact pages, not just links.",
        "category": "Search",
        "icon": "🔬",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "exa-mcp-server"],
        "env_vars": {"EXA_API_KEY": ""},
        "install_note": "Get API key at dashboard.exa.ai/api-keys (free tier available)",
        "package": "exa-mcp-server",
        "popular": True,
    },
    {
        "id": "fetch",
        "name": "Web Fetch",
        "description": "Fetch and convert web pages to markdown. No API key needed.",
        "category": "Search",
        "icon": "🌐",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "env_vars": {},
        "install_note": "No API key needed. Requires uvx (pip install uv).",
        "package": "mcp-server-fetch",
        "popular": True,
    },
    {
        "id": "context7",
        "name": "Context7",
        "description": "Pulls up-to-date docs for any library straight into your context. No more outdated API hallucinations.",
        "category": "Search",
        "icon": "📚",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
        "env_vars": {},
        "install_note": "No API key needed. Indexes live documentation for 1000+ libraries.",
        "package": "@upstash/context7-mcp",
        "popular": True,
    },
    # ─── Data ────────────────────────────────────────────────────────────────
    {
        "id": "postgres",
        "name": "PostgreSQL",
        "description": "Query PostgreSQL databases with read/write access and schema introspection.",
        "category": "Data",
        "icon": "🐘",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"],
        "env_vars": {},
        "install_note": "Replace the connection string in args with your database URL.",
        "package": "@modelcontextprotocol/server-postgres",
        "popular": False,
    },
    {
        "id": "sqlite",
        "name": "SQLite",
        "description": "Query and write to local SQLite databases.",
        "category": "Data",
        "icon": "🗃️",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sqlite", "--db-path", "/tmp/mydb.sqlite"],
        "env_vars": {},
        "install_note": "Change --db-path to your database file location.",
        "package": "@modelcontextprotocol/server-sqlite",
        "popular": False,
    },
    {
        "id": "supabase",
        "name": "Supabase",
        "description": "Full Supabase access — database, auth, edge functions, storage, and realtime APIs.",
        "category": "Data",
        "icon": "⚡",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@supabase/mcp-server-supabase", "--access-token", "YOUR_TOKEN"],
        "env_vars": {"SUPABASE_ACCESS_TOKEN": ""},
        "install_note": "Get access token at supabase.com/dashboard/account/tokens",
        "package": "@supabase/mcp-server-supabase",
        "popular": True,
    },
    {
        "id": "filesystem",
        "name": "Filesystem",
        "description": "Read, write, and manage local files. Scoped to allowed directories.",
        "category": "Data",
        "icon": "📁",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home"],
        "env_vars": {},
        "install_note": "Change /home to the directory you want to allow access to.",
        "package": "@modelcontextprotocol/server-filesystem",
        "popular": True,
    },
    {
        "id": "memory",
        "name": "Memory",
        "description": "Knowledge graph-based persistent memory — agents can store and recall facts across sessions.",
        "category": "Data",
        "icon": "🧠",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "env_vars": {},
        "install_note": "No configuration needed. Stores data in a local knowledge graph file.",
        "package": "@modelcontextprotocol/server-memory",
        "popular": True,
    },
    # ─── Cloud ───────────────────────────────────────────────────────────────
    {
        "id": "cloudflare",
        "name": "Cloudflare",
        "description": "Manage Cloudflare Workers, KV, R2, DNS, D1 databases, and Pages.",
        "category": "Cloud",
        "icon": "☁️",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@cloudflare/mcp-server-cloudflare"],
        "env_vars": {"CLOUDFLARE_API_TOKEN": "", "CLOUDFLARE_ACCOUNT_ID": ""},
        "install_note": "Create a token at dash.cloudflare.com/profile/api-tokens",
        "package": "@cloudflare/mcp-server-cloudflare",
        "popular": True,
    },
    {
        "id": "docker",
        "name": "Docker",
        "description": "Manage Docker containers, images, volumes, and compose stacks.",
        "category": "Cloud",
        "icon": "🐳",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@ckreiling/mcp-server-docker"],
        "env_vars": {},
        "install_note": "Requires Docker daemon running locally.",
        "package": "@ckreiling/mcp-server-docker",
        "popular": False,
    },
    {
        "id": "kubernetes",
        "name": "Kubernetes",
        "description": "Manage Kubernetes clusters — pods, deployments, services, namespaces, logs.",
        "category": "Cloud",
        "icon": "⚙️",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@flux159/mcp-server-kubernetes"],
        "env_vars": {},
        "install_note": "Uses your existing ~/.kube/config. No extra credentials needed.",
        "package": "@flux159/mcp-server-kubernetes",
        "popular": False,
    },
    {
        "id": "aws-kb",
        "name": "AWS Knowledge Base",
        "description": "Retrieve information from AWS Bedrock Knowledge Bases using RAG.",
        "category": "Cloud",
        "icon": "🟠",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@aws/bedrock-kb-retrieval-mcp-server"],
        "env_vars": {"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": "", "AWS_REGION": "us-east-1"},
        "install_note": "Requires AWS credentials and a Bedrock Knowledge Base already set up.",
        "package": "@aws/bedrock-kb-retrieval-mcp-server",
        "popular": False,
    },
    # ─── AI ──────────────────────────────────────────────────────────────────
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "description": "Dynamic, reflective problem-solving through structured thought sequences. Boosts reasoning quality.",
        "category": "AI",
        "icon": "🧩",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "env_vars": {},
        "install_note": "No configuration needed. Official MCP reference server.",
        "package": "@modelcontextprotocol/server-sequential-thinking",
        "popular": True,
    },
    {
        "id": "huggingface",
        "name": "Hugging Face",
        "description": "Search models, datasets, and papers on Hugging Face Hub. Generate images with Flux.",
        "category": "AI",
        "icon": "🤗",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@huggingface/mcp-client"],
        "env_vars": {"HF_TOKEN": ""},
        "install_note": "Get token at huggingface.co/settings/tokens (free account works)",
        "package": "@huggingface/mcp-client",
        "popular": True,
    },
    # ─── Productivity ─────────────────────────────────────────────────────────
    {
        "id": "google-maps",
        "name": "Google Maps",
        "description": "Geocoding, directions, place search, and distance matrix.",
        "category": "Productivity",
        "icon": "🗺️",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-google-maps"],
        "env_vars": {"GOOGLE_MAPS_API_KEY": ""},
        "install_note": "Get API key at console.cloud.google.com (Maps JavaScript API)",
        "package": "@modelcontextprotocol/server-google-maps",
        "popular": False,
    },
    {
        "id": "time",
        "name": "Time & Timezone",
        "description": "Current time, timezone conversion, and time calculations. No API key.",
        "category": "Productivity",
        "icon": "🕐",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-time"],
        "env_vars": {},
        "install_note": "No API key needed.",
        "package": "@modelcontextprotocol/server-time",
        "popular": False,
    },
    {
        "id": "stripe",
        "name": "Stripe",
        "description": "Query Stripe payments, customers, subscriptions, and invoices.",
        "category": "Productivity",
        "icon": "💳",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@stripe/mcp", "--tools=all"],
        "env_vars": {"STRIPE_SECRET_KEY": ""},
        "install_note": "Get secret key at dashboard.stripe.com/apikeys (use test key first)",
        "package": "@stripe/mcp",
        "popular": True,
    },
    {
        "id": "jira",
        "name": "Jira",
        "description": "Create and manage Jira issues, sprints, epics, and project boards.",
        "category": "Productivity",
        "icon": "🎯",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@sooperset/mcp-atlassian"],
        "env_vars": {"JIRA_URL": "", "JIRA_EMAIL": "", "JIRA_API_TOKEN": ""},
        "install_note": "Create an API token at id.atlassian.com/manage-profile/security/api-tokens",
        "package": "@sooperset/mcp-atlassian",
        "popular": True,
    },
]

# Pre-built skills
SKILLS = [
    {
        "id": "daily-standup",
        "name": "Daily Standup",
        "description": "Run a structured daily standup: what was done, what's next, blockers.",
        "category": "Skills",
        "icon": "🌅",
        "content": """---
name: daily-standup
description: Run a structured daily standup meeting summary
---

# Daily Standup

Ask the user for their standup updates using this structure:

## Yesterday
What did you complete yesterday?

## Today
What are you working on today?

## Blockers
Any blockers or help needed?

After collecting answers, format a clean standup message they can paste into Slack or send to their team.
""",
    },
    {
        "id": "code-review",
        "name": "Code Review",
        "description": "Systematic code review covering correctness, security, performance, readability.",
        "category": "Skills",
        "icon": "🔎",
        "content": """---
name: code-review
description: Systematic code review covering correctness, security, performance, readability
---

# Code Review Checklist

Review the provided code diff or file for:

## Correctness
- Logic errors, edge cases, off-by-one errors
- Error handling completeness
- Input validation

## Security
- Injection vulnerabilities (SQL, XSS, command injection)
- Authentication/authorization issues
- Sensitive data exposure

## Performance
- N+1 queries, unnecessary loops
- Missing indexes, caching opportunities
- Memory leaks

## Readability
- Clear naming, appropriate comments
- Function length and complexity
- Consistency with existing patterns

Provide specific line-level feedback with suggested improvements.
""",
    },
    {
        "id": "bug-report",
        "name": "Bug Report",
        "description": "Guide the user to write a detailed, actionable bug report.",
        "category": "Skills",
        "icon": "🐛",
        "content": """---
name: bug-report
description: Guide the user to write a detailed, actionable bug report
---

# Bug Report Assistant

Help the user create a complete bug report by asking for:

1. **Summary**: One sentence describing the bug
2. **Steps to reproduce**: Numbered list of exact steps
3. **Expected behavior**: What should happen
4. **Actual behavior**: What actually happens (include error messages)
5. **Environment**: OS, browser/runtime version, relevant config
6. **Screenshots/logs**: Any relevant output

Format the final report in Markdown ready to paste into GitHub Issues, Linear, or Jira.
""",
    },
    {
        "id": "deploy-checklist",
        "name": "Deploy Checklist",
        "description": "Pre-deployment checklist to catch issues before they reach production.",
        "category": "Skills",
        "icon": "🚀",
        "content": """---
name: deploy-checklist
description: Pre-deployment checklist to catch issues before they reach production
---

# Pre-Deployment Checklist

Walk through these checks before deploying:

## Code
- [ ] All tests passing (unit, integration, e2e)
- [ ] Code reviewed and approved
- [ ] No debug/console.log statements left in
- [ ] Feature flags configured correctly

## Database
- [ ] Migrations tested on staging
- [ ] Rollback plan for schema changes
- [ ] No breaking changes to existing data

## Config
- [ ] Environment variables set in production
- [ ] Secrets rotated if needed
- [ ] API rate limits checked

## Monitoring
- [ ] Error tracking configured
- [ ] Alerts set up for key metrics
- [ ] Rollback procedure documented

Ask the user which items need attention and help them resolve issues before deploying.
""",
    },
    {
        "id": "meeting-notes",
        "name": "Meeting Notes",
        "description": "Structure meeting notes with agenda, decisions, and action items.",
        "category": "Skills",
        "icon": "📝",
        "content": """---
name: meeting-notes
description: Structure meeting notes with agenda, decisions, and action items
---

# Meeting Notes Assistant

Help structure meeting notes. Ask for:

1. **Meeting title and date**
2. **Attendees**
3. **Raw notes or discussion points**

Then organize into:

## Summary
Brief 2-3 sentence summary of the meeting.

## Decisions Made
Bulleted list of decisions that were reached.

## Action Items
| Task | Owner | Due Date |
|------|-------|----------|
| ... | ... | ... |

## Next Steps
What happens next, and when is the follow-up?
""",
    },
    {
        "id": "git-commit",
        "name": "Git Commit Message",
        "description": "Write clear, conventional git commit messages from a diff or description.",
        "category": "Skills",
        "icon": "✍️",
        "content": """---
name: git-commit
description: Write clear, conventional git commit messages from a diff or description
---

# Git Commit Message Writer

Given a diff or description of changes, write a commit message following the Conventional Commits spec.

## Format
```
<type>(<scope>): <short summary>

<body — what and why, not how>

<footer — breaking changes, closes #issue>
```

## Types
- `feat` — new feature
- `fix` — bug fix
- `refactor` — code change that neither fixes a bug nor adds a feature
- `docs` — documentation only
- `test` — adding or correcting tests
- `chore` — build process, tooling, dependencies

## Rules
- Subject line: 50 chars max, imperative mood ("add" not "added")
- Body: wrap at 72 chars, explain *why* not *what*
- Reference issues: `Closes #123`

Ask the user for the diff or description of their changes, then generate the commit message.
""",
    },
    {
        "id": "api-docs",
        "name": "API Documentation",
        "description": "Generate clear API documentation from code or a description of endpoints.",
        "category": "Skills",
        "icon": "📖",
        "content": """---
name: api-docs
description: Generate clear API documentation from code or a description of endpoints
---

# API Documentation Generator

Given source code or an endpoint description, generate clean API docs in this format:

## Endpoint: `METHOD /path`

**Description:** What this endpoint does.

**Authentication:** Bearer token / API key / None

**Request Body:**
```json
{
  "field": "type — description"
}
```

**Response (200):**
```json
{
  "field": "type — description"
}
```

**Errors:**
| Code | Meaning |
|------|---------|
| 400 | Bad request — invalid input |
| 401 | Unauthorized |
| 404 | Not found |

**Example:**
```bash
curl -X POST https://api.example.com/endpoint \\
  -H "Authorization: Bearer TOKEN" \\
  -d '{"field": "value"}'
```

Ask the user to provide their code or endpoint details.
""",
    },
    {
        "id": "incident-response",
        "name": "Incident Response",
        "description": "Guide through incident response: triage, mitigation, communication, postmortem.",
        "category": "Skills",
        "icon": "🚨",
        "content": """---
name: incident-response
description: Guide through incident response: triage, mitigation, communication, postmortem
---

# Incident Response Guide

## 1. Triage (first 5 minutes)
- What is broken? (service, feature, data)
- Who is affected? (users, internal, all)
- Severity: P1 (all users down) / P2 (partial) / P3 (minor)
- When did it start?

## 2. Immediate Mitigation
- Can we roll back the last deployment?
- Can we disable the feature flag?
- Can we redirect traffic?

## 3. Communication
Draft a status update:
> **[INCIDENT]** We are investigating an issue with [service]. Users may experience [symptom]. We will update in 15 minutes.

## 4. Investigation
- Check recent deployments (last 2 hours)
- Check error rates in monitoring
- Check logs for exceptions
- Check dependent services

## 5. Resolution
- Document what the fix was
- Verify metrics return to normal
- Post all-clear message

## 6. Postmortem (within 48h)
- Timeline of events
- Root cause
- What worked / what didn't
- Action items to prevent recurrence

Ask the user what's happening and guide them through each step.
""",
    },
    {
        "id": "sprint-planning",
        "name": "Sprint Planning",
        "description": "Facilitate sprint planning: capacity, story point estimation, backlog prioritization.",
        "category": "Skills",
        "icon": "📊",
        "content": """---
name: sprint-planning
description: Facilitate sprint planning: capacity, story point estimation, backlog prioritization
---

# Sprint Planning Facilitator

Help the team plan a sprint effectively.

## Step 1: Capacity
- How many engineers? How many days in the sprint?
- Subtract: holidays, on-call, meetings (~20% overhead)
- Available capacity = engineers × days × 0.8

## Step 2: Velocity
- What was the team's average velocity last 3 sprints?
- Use this as the target story points for this sprint.

## Step 3: Backlog Review
For each story, estimate:
- **1 pt** — trivial (< 2 hours)
- **2 pt** — small (half a day)
- **3 pt** — medium (1-2 days)
- **5 pt** — large (3-4 days)
- **8 pt** — needs splitting

## Step 4: Prioritization
Order by: business value × urgency / effort

## Step 5: Sprint Goal
One sentence describing what the team will accomplish.

Ask the user for their backlog items and team details, then help them build the sprint.
""",
    },
    {
        "id": "code-explain",
        "name": "Code Explainer",
        "description": "Explain code clearly at any level — beginner-friendly or technical deep-dive.",
        "category": "Skills",
        "icon": "💡",
        "content": """---
name: code-explain
description: Explain code clearly at any level — beginner-friendly or technical deep-dive
---

# Code Explainer

Given a block of code, explain it clearly.

## What to cover:
1. **What it does** — plain English summary in 1-2 sentences
2. **How it works** — step through the logic
3. **Key concepts** — explain any patterns, algorithms, or language features used
4. **Gotchas** — any non-obvious behavior, edge cases, or performance considerations
5. **Example** — show it with sample input/output if helpful

## Tone adjustment:
- If the user is a beginner: use analogies, avoid jargon, explain every concept
- If the user is experienced: be concise, focus on the non-obvious parts
- If asked for a deep-dive: include time/space complexity, alternative approaches

Ask the user to paste the code and tell you their experience level.
""",
    },
    {
        "id": "frontend-design",
        "name": "Frontend Design",
        "description": "Guide creation of distinctive, production-grade frontend UIs that avoid generic aesthetics.",
        "category": "Skills",
        "icon": "🎨",
        "content": """---
name: frontend-design
description: Guide creation of distinctive, production-grade frontend UIs that avoid generic aesthetics
---

# Frontend Design

Create visually striking, production-ready interfaces with intentional aesthetics.

## Before Coding

Commit to an aesthetic direction by answering:
- **Purpose**: What problem does this UI solve and who uses it?
- **Tone**: Pick an extreme aesthetic (brutalist, retro-futuristic, minimalist, etc.)
- **Constraints**: Accessibility requirements, tech stack limits.
- **Differentiation**: What makes this design unforgettable?

## Implementation Rules

- Define all colours via CSS custom properties (no magic hex values).
- Choose distinctive typography — avoid Inter, Roboto, and other defaults.
- Add purposeful motion/animation that reinforces the aesthetic.
- Match implementation complexity to the vision (maximalist = elaborate code).

## Avoid

- Purple gradients and generic SaaS colour schemes.
- Predictable card-grid layouts without context-specific justification.
- Overused font stacks with no personality.
""",
    },
    {
        "id": "seo-audit",
        "name": "SEO Audit",
        "description": "Identify and prioritise SEO issues across crawlability, on-page elements, content quality, and links.",
        "category": "Skills",
        "icon": "🔍",
        "content": """---
name: seo-audit
description: Identify and prioritise SEO issues across crawlability, on-page elements, content quality, and links
---

# SEO Audit

Expert-level SEO analysis and actionable recommendations.

## Audit Framework (in priority order)

1. **Crawlability & Indexation** — robots.txt, sitemaps, noindex tags.
2. **Technical Foundations** — Core Web Vitals, mobile-friendliness, HTTPS.
3. **On-Page Optimisation** — title tags, meta descriptions, heading hierarchy.
4. **Content Quality** — E-E-A-T signals, depth, duplication.
5. **Authority & Links** — internal linking, backlink profile.

## Site-Type Variations

Adjust recommendations for: SaaS, e-commerce, blog, local business.

## Note

Use Google's Rich Results Test for accurate structured data / schema validation.

Ask the user for the URL or page content to audit, then work through each category.
""",
    },
    {
        "id": "copywriting",
        "name": "Copywriting",
        "description": "Produce conversion-focused marketing copy for landing pages, homepages, and pricing pages.",
        "category": "Skills",
        "icon": "✒️",
        "content": """---
name: copywriting
description: Produce conversion-focused marketing copy for landing pages, homepages, and pricing pages
---

# Copywriting

Write clear, compelling, conversion-focused copy that drives action.

## Gather Context First

- Page purpose and primary CTA
- Target audience and their main pain point
- Product / feature being promoted
- Primary traffic source (ads, organic, email)

## Core Principles

- Clarity over cleverness — say the thing plainly.
- Specificity over vagueness — use real numbers and concrete benefits.
- Active voice; short sentences.
- Lead with customer benefit, not product features.
- Honest claims only — no superlatives without proof.

## Page Structure

Headline → Subheadline → Value prop → Social proof → CTA → Objection handling

## Deliverable

Provide copy organised by section with annotations explaining each choice,
plus one or two alternative headline options.
""",
    },
    {
        "id": "pdf",
        "name": "PDF Processor",
        "description": "Read, extract, merge, split, watermark, and create PDFs using Python libraries.",
        "category": "Skills",
        "icon": "📄",
        "content": """---
name: pdf
description: Read, extract, merge, split, watermark, and create PDFs using Python libraries
---

# PDF Processor

Handle PDF operations with Python libraries and CLI tools.

## Python Libraries

| Library | Best For |
|---------|----------|
| `pypdf` | Merge, split, rotate, metadata |
| `pdfplumber` | Text extraction with layout; table extraction |
| `reportlab` | Create PDFs from scratch, formatted output |

## CLI Tools

- `pdftotext` (poppler-utils) — fast plain-text extraction
- `qpdf` — encryption, decryption
- `pdftk` — form filling, stamping, burst

## Common Tasks

- Scanned PDF OCR → use Tesseract + pdfplumber
- Merge N files → `pypdf.PdfMerger`
- Add watermark → `reportlab` overlay + `pypdf`
- Extract tables → `pdfplumber.extract_tables()`

Ask the user what PDF operation they need, then implement it step by step.
""",
    },
    {
        "id": "summarize",
        "name": "Summarize",
        "description": "Condense long documents, articles, and conversation threads into concise summaries.",
        "category": "Skills",
        "icon": "📋",
        "content": """---
name: summarize
description: Condense long documents, articles, and conversation threads into concise summaries
---

# Summarize

Process long-form content into concise, actionable summaries.

## Output Modes

| Mode | Use When |
|------|----------|
| `bullet` | Quick scan, list of key points |
| `executive` | Leadership audience, 3–5 sentence overview |
| `detailed` | Deep dive, preserves nuance and examples |
| `action-items` | Meeting notes, extract next steps and owners |

## Usage

Specify mode explicitly. Default to `bullet` if unspecified.

Preserve: key decisions, numbers/statistics, named entities, deadlines.
Omit: filler phrases, repeated context, tangential anecdotes.

Ask the user to paste the content and specify the output mode if they have a preference.
""",
    },
    {
        "id": "brainstorming",
        "name": "Brainstorming",
        "description": "Turn rough ideas into fully formed designs and specs through structured collaborative dialogue.",
        "category": "Skills",
        "icon": "🧠",
        "content": """---
name: brainstorming
description: Turn rough ideas into fully formed designs and specs through structured collaborative dialogue
---

# Brainstorming

Structured ideation that turns vague ideas into documented design specs.

## Process

1. **Explore** — Understand project context through open questions (one at a time).
2. **Diverge** — Propose 2–3 alternative approaches with clear trade-offs.
3. **Converge** — Present a recommended direction for user approval.
4. **Specify** — Create a documented design spec.
5. **Review** — Conduct a spec review to catch unexamined assumptions.
6. **Plan** — Transition to implementation planning.

## Key Rule

Even "simple" projects require a formal design step — unexamined assumptions
are the leading cause of wasted implementation effort.

Start by asking: "What problem are you trying to solve, and who has that problem?"
""",
    },
    {
        "id": "ppt-creator",
        "name": "PPT Creator",
        "description": "Generate professional PowerPoint presentations from a topic or document with charts and speaker notes.",
        "category": "Skills",
        "icon": "📊",
        "content": """---
name: ppt-creator
description: Generate professional PowerPoint presentations from a topic or document with charts and speaker notes
---

# PPT Creator

Create structured, professional PPTX presentations from natural language input.

## Inputs Accepted

- Topic or title (generates outline automatically)
- Existing document or notes (converts to slides)
- Structured outline (maps directly to slide structure)

## Output Includes

- Title slide + agenda slide
- Content slides with headings and bullet points
- Data-driven charts where numerical data is present
- Speaker notes per slide

## Guidelines

- Ask for target audience and desired tone before generating.
- Keep bullets to ≤ 6 words each; elaborate in speaker notes.
- Limit to 10–15 slides unless the user specifies otherwise.

Use the `python-pptx` library to generate the PPTX file programmatically.
""",
    },
    {
        "id": "audit-website",
        "name": "Website Audit",
        "description": "Comprehensive website audit covering SEO, performance, security, accessibility, and content quality.",
        "category": "Skills",
        "icon": "🔬",
        "content": """---
name: audit-website
description: Comprehensive website audit covering SEO, performance, security, accessibility, and content quality
---

# Website Audit

Audit a live website across key quality dimensions.

## Categories

1. **SEO** — title tags, meta, canonicals, sitemap, robots.txt
2. **Performance** — Core Web Vitals, page size, image optimisation, caching
3. **Security** — HTTPS, security headers (CSP, HSTS, X-Frame-Options)
4. **Accessibility** — alt text, ARIA labels, colour contrast, keyboard navigation
5. **Content** — broken links, duplicate content, readability score
6. **Mobile** — responsive design, tap targets, viewport meta

## Scoring

Rate each category 0–100. Target: **85+** for standard sites, **95+** for high-traffic.

## Deliverable

- Summary score per category
- Top 5 issues by severity
- Specific fix recommendations with priority order

Ask for the URL, then work through each category systematically.
""",
    },
]


def get_server(server_id: str) -> dict | None:
    """Get a marketplace server by ID."""
    return next((s for s in MCP_SERVERS if s["id"] == server_id), None)


def get_skill(skill_id: str) -> dict | None:
    """Get a marketplace skill by ID."""
    return next((s for s in SKILLS if s["id"] == skill_id), None)


def get_all_items(category: str = "All") -> list[dict]:
    """Get all marketplace items, optionally filtered by category."""
    servers = [{"item_type": "server", **s} for s in MCP_SERVERS]
    skills = [{"item_type": "skill", **s} for s in SKILLS]
    all_items = servers + skills
    if category == "All":
        return all_items
    if category == "Skills":
        return skills
    return [s for s in servers if s["category"] == category]


# ---------------------------------------------------------------------------
# Smithery dynamic fetcher
# ---------------------------------------------------------------------------

class SmitheryFetcher:
    BASE_URL = "https://registry.smithery.ai/servers"

    def fetch(self, query: str = "", limit: int = 20) -> list[dict]:
        """Fetch servers from Smithery registry, normalize to marketplace format."""
        import httpx
        try:
            params = {"limit": limit}
            if query:
                params["q"] = query
            resp = httpx.get(self.BASE_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            servers = data.get("servers", data if isinstance(data, list) else [])
            return [self._normalize(s) for s in servers if s]
        except Exception as e:
            from tooldns.config import logger
            logger.warning(f"Smithery fetch failed: {e}")
            return []

    def _normalize(self, s: dict) -> dict:
        """Convert Smithery server format to ToolsDNS marketplace format."""
        return {
            "id": s.get("qualifiedName", s.get("name", "")).replace("/", "-").replace("@", ""),
            "name": s.get("displayName") or s.get("name", "Unknown"),
            "description": s.get("description", ""),
            "category": "Search & Web",  # default; can be improved with tag mapping
            "icon": "🔌",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", s.get("qualifiedName", "")],
            "env_vars": {},
            "install_note": f"Install from Smithery: {s.get('qualifiedName', '')}",
            "package": s.get("qualifiedName", ""),
            "popular": s.get("useCount", 0) > 1000,
            "source": "smithery",
        }


_smithery = SmitheryFetcher()


def get_dynamic_servers(query: str = "", limit: int = 20) -> list[dict]:
    """Fetch from Smithery and merge with curated list (curated takes priority)."""
    curated_packages = {s.get("package", "") for s in MCP_SERVERS}
    dynamic = _smithery.fetch(query=query, limit=limit)
    # Only add dynamic servers not already in curated list
    new_servers = [s for s in dynamic if s.get("package", "") not in curated_packages]
    return MCP_SERVERS + new_servers
