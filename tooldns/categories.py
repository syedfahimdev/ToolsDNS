"""
categories.py — Tool categorization for ToolsDNS.

Assigns a human-readable category to every indexed tool based on
its name, description, and source. Categories are shown in the
Browse Tools UI and returned in the search API.
"""

# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------

CATEGORIES = [
    "Dev & Code",
    "Communication",
    "Productivity",
    "Files & Docs",
    "Data & Analytics",
    "Media & Content",
    "CRM & Sales",
    "Finance",
    "E-commerce",
    "Design & UI",
    "AI & Agents",
    "Search & Web",
    "DevOps & Infra",
    "Skills",
    "Other",
]

# Service prefix → category (from Composio tool naming: SERVICE_ACTION)
_PREFIX_MAP: dict[str, str] = {
    # Dev & Code
    "GITHUB": "Dev & Code",
    "GITLAB": "Dev & Code",
    "BITBUCKET": "Dev & Code",
    "JIRA": "Dev & Code",
    "LINEAR": "Dev & Code",
    "SUPABASE": "Dev & Code",
    "CODEINTERPRETER": "Dev & Code",
    "HEROKU": "Dev & Code",
    "VERCEL": "Dev & Code",
    "NETLIFY": "Dev & Code",

    # Communication
    "GMAIL": "Communication",
    "OUTLOOK": "Communication",
    "DISCORD": "Communication",
    "SLACK": "Communication",
    "TEAMS": "Communication",
    "TELEGRAM": "Communication",
    "WHATSAPP": "Communication",
    "TWILIO": "Communication",
    "SENDGRID": "Communication",
    "MAILCHIMP": "Communication",
    "INTERCOM": "Communication",
    "ZENDESK": "Communication",

    # Productivity
    "GOOGLECALENDAR": "Productivity",
    "GOOGLETASKS": "Productivity",
    "NOTION": "Productivity",
    "AIRTABLE": "Productivity",
    "TODOIST": "Productivity",
    "ASANA": "Productivity",
    "TRELLO": "Productivity",
    "MONDAY": "Productivity",
    "CLICKUP": "Productivity",
    "CALENDAR": "Productivity",

    # Files & Docs
    "GOOGLEDRIVE": "Files & Docs",
    "GOOGLEDOCS": "Files & Docs",
    "GOOGLESHEETS": "Files & Docs",
    "DROPBOX": "Files & Docs",
    "BOX": "Files & Docs",
    "ONEDRIVE": "Files & Docs",
    "SHAREPOINT": "Files & Docs",
    "CONFLUENCE": "Files & Docs",
    "DOCUSIGN": "Files & Docs",
    "PDFCO": "Files & Docs",

    # Data & Analytics
    "BIGQUERY": "Data & Analytics",
    "SNOWFLAKE": "Data & Analytics",
    "DATABRICKS": "Data & Analytics",
    "SEGMENT": "Data & Analytics",
    "MIXPANEL": "Data & Analytics",
    "AMPLITUDE": "Data & Analytics",
    "POSTHOG": "Data & Analytics",
    "METABASE": "Data & Analytics",
    "TABLEAU": "Data & Analytics",
    "LOOKER": "Data & Analytics",
    "MONGO": "Data & Analytics",
    "POSTGRES": "Data & Analytics",
    "MYSQL": "Data & Analytics",
    "REDIS": "Data & Analytics",

    # Media & Content
    "YOUTUBE": "Media & Content",
    "ELEVENLABS": "Media & Content",
    "SPOTIFY": "Media & Content",
    "TWITTER": "Media & Content",
    "INSTAGRAM": "Media & Content",
    "TIKTOK": "Media & Content",
    "PINTEREST": "Media & Content",
    "WORDPRESS": "Media & Content",
    "MEDIUM": "Media & Content",
    "OPENAI": "Media & Content",
    "STABILITY": "Media & Content",
    "DALLE": "Media & Content",
    "REPLICATE": "Media & Content",

    # CRM & Sales
    "SALESFORCE": "CRM & Sales",
    "HUBSPOT": "CRM & Sales",
    "PIPEDRIVE": "CRM & Sales",
    "ZOHO": "CRM & Sales",
    "LINKEDIN": "CRM & Sales",
    "APOLLO": "CRM & Sales",
    "OUTREACH": "CRM & Sales",
    "SALESLOFT": "CRM & Sales",
    "CLOSE": "CRM & Sales",

    # Finance
    "STRIPE": "Finance",
    "PAYPAL": "Finance",
    "QUICKBOOKS": "Finance",
    "XERO": "Finance",
    "PLAID": "Finance",
    "BREX": "Finance",
    "MERCURY": "Finance",
    "EXPENSIFY": "Finance",

    # E-commerce
    "SHOPIFY": "E-commerce",
    "WOOCOMMERCE": "E-commerce",
    "AMAZON": "E-commerce",
    "EBAY": "E-commerce",
    "ETSY": "E-commerce",

    # Design & UI
    "FIGMA": "Design & UI",
    "CANVA": "Design & UI",
    "MIRO": "Design & UI",
    "WEBFLOW": "Design & UI",
    "FRAMER": "Design & UI",

    # AI & Agents
    "COMPOSIO": "AI & Agents",
    "ANTHROPIC": "AI & Agents",
    "GEMINI": "AI & Agents",
    "PERPLEXITY": "AI & Agents",
    "DEEPWIKI": "AI & Agents",

    # Search & Web
    "TAVILY": "Search & Web",
    "BRAVE": "Search & Web",
    "BING": "Search & Web",
    "GOOGLE": "Search & Web",
    "HACKERNEWS": "Search & Web",
    "REDDIT": "Search & Web",
    "YELP": "Search & Web",
    "BROWSER": "Search & Web",
    "FETCH": "Search & Web",
    "HTTPBIN": "Search & Web",
    "TEXT": "Search & Web",

    # DevOps & Infra
    "AWS": "DevOps & Infra",
    "GCP": "DevOps & Infra",
    "AZURE": "DevOps & Infra",
    "CLOUDFLARE": "DevOps & Infra",
    "DATADOG": "DevOps & Infra",
    "PAGERDUTY": "DevOps & Infra",
    "SENTRY": "DevOps & Infra",
    "GRAFANA": "DevOps & Infra",
    "TERRAFORM": "DevOps & Infra",
    "DOCKER": "DevOps & Infra",
    "KUBERNETES": "DevOps & Infra",
    "MONITOR": "DevOps & Infra",
}

# Keyword patterns in name/description → category (fallback when prefix doesn't match)
_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["email", "inbox", "smtp", "imap", "mail"], "Communication"),
    (["calendar", "meeting", "schedule", "event", "appointment"], "Productivity"),
    (["slack", "discord", "chat", "message", "dm"], "Communication"),
    (["github", "gitlab", "commit", "pull request", "repository", "repo"], "Dev & Code"),
    (["database", "sql", "query", "table", "schema"], "Data & Analytics"),
    (["spreadsheet", "sheet", "excel", "csv"], "Files & Docs"),
    (["document", "doc", "pdf", "file", "drive", "folder"], "Files & Docs"),
    (["video", "audio", "music", "podcast", "stream", "youtube"], "Media & Content"),
    (["image", "photo", "generate image", "dalle", "stable diffusion"], "Media & Content"),
    (["voice", "speech", "tts", "text to speech", "elevenlabs"], "Media & Content"),
    (["search", "browse", "web", "scrape", "crawl", "fetch"], "Search & Web"),
    (["crm", "lead", "deal", "pipeline", "salesforce", "hubspot"], "CRM & Sales"),
    (["invoice", "payment", "billing", "stripe", "paypal", "finance"], "Finance"),
    (["shopify", "ecommerce", "product", "order", "inventory"], "E-commerce"),
    (["design", "figma", "ui", "prototype", "wireframe"], "Design & UI"),
    (["deploy", "server", "cloud", "infrastructure", "devops", "ci/cd"], "DevOps & Infra"),
    (["agent", "skill", "workflow", "automation", "ai tool"], "AI & Agents"),
    (["task", "todo", "project", "kanban", "sprint", "notion"], "Productivity"),
    (["analytics", "metrics", "dashboard", "report", "tracking"], "Data & Analytics"),
]


def categorize_tool(name: str, description: str, source_info: dict) -> str:
    """
    Assign a category to a tool based on its name, description, and source.

    Priority order:
    1. Source type = skill → always "Skills"
    2. Tool name prefix (e.g. GITHUB_ → Dev & Code)
    3. Keyword scan in name + description
    4. Fallback → "Other"

    Args:
        name: Tool name (e.g. "GITHUB_CREATE_ISSUE" or "everi-work-order")
        description: Tool description text
        source_info: Provenance metadata dict

    Returns:
        str: Category name from CATEGORIES list
    """
    # Skills always get their own category
    source_type = source_info.get("source_type", "") or ""
    if "skill" in source_type.lower():
        return "Skills"

    # Check uppercase prefix (Composio-style: GITHUB_CREATE_ISSUE)
    name_upper = name.upper()
    for prefix, category in _PREFIX_MAP.items():
        if name_upper.startswith(prefix + "_") or name_upper == prefix:
            return category

    # Keyword scan across name + description
    text = (name + " " + description).lower()
    for keywords, category in _KEYWORD_RULES:
        if any(kw in text for kw in keywords):
            return category

    return "Other"
