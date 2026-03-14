"""
marketplace.py — Curated catalog of popular MCP servers and skills.

Used by the web UI marketplace page to let users browse and one-click
install popular MCP servers and pre-built skills into ToolDNS.
"""

# Categories for filtering
CATEGORIES = ["All", "Dev", "Communication", "Search", "Data", "Cloud", "Productivity", "Skills"]

# Curated MCP servers
MCP_SERVERS = [
    # ─── Dev ─────────────────────────────────────────────────────────────────
    {
        "id": "github",
        "name": "GitHub",
        "description": "Create issues, PRs, review code, search repositories, manage GitHub.",
        "category": "Dev",
        "icon": "🐙",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env_vars": {"GITHUB_TOKEN": ""},
        "install_note": "Create a token at github.com/settings/tokens (needs repo scope)",
        "package": "@modelcontextprotocol/server-github",
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
        "id": "fetch",
        "name": "Web Fetch",
        "description": "Fetch and convert web pages to markdown. No API key needed.",
        "category": "Search",
        "icon": "🌐",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "env_vars": {},
        "install_note": "No API key needed. Requires Python uvx (pip install uv).",
        "package": "mcp-server-fetch",
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
        "description": "Persistent key-value memory store that agents can read and write across sessions.",
        "category": "Data",
        "icon": "🧠",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "env_vars": {},
        "install_note": "No configuration needed.",
        "package": "@modelcontextprotocol/server-memory",
        "popular": True,
    },
    # ─── Cloud ───────────────────────────────────────────────────────────────
    {
        "id": "cloudflare",
        "name": "Cloudflare",
        "description": "Manage Cloudflare Workers, KV, R2, DNS, and D1 databases.",
        "category": "Cloud",
        "icon": "☁️",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@cloudflare/mcp-server-cloudflare"],
        "env_vars": {"CLOUDFLARE_API_TOKEN": "", "CLOUDFLARE_ACCOUNT_ID": ""},
        "install_note": "Create a token at dash.cloudflare.com/profile/api-tokens",
        "package": "@cloudflare/mcp-server-cloudflare",
        "popular": False,
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
        "command": "uvx",
        "args": ["mcp-server-time"],
        "env_vars": {},
        "install_note": "No API key needed. Requires uvx (pip install uv).",
        "package": "mcp-server-time",
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
