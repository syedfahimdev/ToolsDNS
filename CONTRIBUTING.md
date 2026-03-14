# Contributing to ToolsDNS

Thank you for helping make ToolsDNS better! Here's how to get started.

## Quick Start

```bash
git clone https://github.com/syedfahimdev/ToolsDNS
cd ToolsDNS
pip install -e ".[dev]"
python -m tooldns.cli setup
python -m tooldns.cli serve
```

Open `http://localhost:8787/ui` to see the dashboard.

## Ways to Contribute

- **Add marketplace skills** — edit `tooldns/marketplace.py`, add to `SKILLS`
- **Add MCP servers** — edit `tooldns/marketplace.py`, add to `MCP_SERVERS`
- **Report bugs** — open a GitHub Issue with steps to reproduce
- **Fix bugs** — open a PR with a clear description
- **Add features** — open an Issue first to discuss, then PR

## Pull Request Guidelines

1. One feature or fix per PR
2. Test your change locally before submitting
3. Update `README.md` if you add a new feature or config option
4. Keep commits clean — squash WIP commits before merging

## Project Structure

```
tooldns/
├── main.py          # FastAPI app entry point + background tasks
├── tooldns/
│   ├── config.py    # All settings (env vars) — add new config here
│   ├── database.py  # SQLite storage — all DB access goes through here
│   ├── ingestion.py # Multi-source tool ingestion pipeline
│   ├── search.py    # Semantic + BM25 hybrid search
│   ├── api.py       # REST API routes (/v1/*)
│   ├── ui.py        # Web UI routes (/ui/*)
│   ├── auth.py      # API key auth + per-key usage tracking
│   ├── marketplace.py # Curated MCP server + skill catalog
│   └── discover.py  # URL auto-discovery
└── templates/       # Jinja2 HTML templates
```

## Adding a Skill to the Marketplace

In `tooldns/marketplace.py`, add to the `SKILLS` list:

```python
{
    "id": "my-skill",
    "name": "My Skill",
    "description": "What this skill does in one sentence.",
    "icon": "🎯",
    "tags": ["category"],
    "content": """---
name: my-skill
description: What this skill does
tags: [category]
---

## Instructions

Step by step instructions for the LLM...

## TEMPLATE
{input}
""",
},
```

## Adding an MCP Server to the Marketplace

In `tooldns/marketplace.py`, add to the `MCP_SERVERS` list:

```python
{
    "id": "my-server",
    "name": "My Server",
    "description": "What it does.",
    "icon": "🔧",
    "category": "Developer Tools",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@scope/my-mcp-server"],
    "package": "@scope/my-mcp-server",
    "popular": False,
},
```
