# Contributing to ToolsDNS

Thank you for helping make ToolsDNS better! This guide gets you from zero to your first merged PR.

## Quick Start

```bash
# Fork on GitHub, then:
git clone https://github.com/YOUR_USERNAME/ToolsDNS.git
cd ToolsDNS
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
toolsdns serve   # → http://localhost:8787/ui
```

## What to Work On

Check the [open issues](https://github.com/syedfahimdev/ToolsDNS/issues) — anything labelled `good first issue` is a great starting point.

| Label | Meaning |
|---|---|
| `good first issue` | Small, well-defined, great for first-timers |
| `help wanted` | We'd love a contributor to pick this up |
| `marketplace` | Adding a new MCP server to the catalog |
| `skill` | Adding or improving a SKILL.md file |
| `bug` | Something broken that needs fixing |

## PR Guidelines

1. **One thing per PR** — focused PRs get reviewed faster
2. **Test it manually** — run the server and verify your change works end-to-end
3. **Don't include unrelated changes** — no reformatting unrelated files
4. **Describe what and why** — a short PR description helps reviewers

## Common Tasks

### Add a Marketplace Server

Edit `tooldns/marketplace.py` — add to the `MARKETPLACE` list:

```python
{
    "id": "your-server",
    "name": "Your Server Name",
    "description": "One sentence: what it does",
    "category": "Dev & Code",  # use an existing category
    "emoji": "🔧",
    "install": {
        "type": "mcp_http",  # or "mcp_stdio"
        "url": "https://your-mcp-server.com/mcp",
        "env_vars": ["YOUR_API_KEY"],
    }
}
```

### Add a Tool Category

Edit `tooldns/categories.py` — add to `_PREFIX_MAP` or `_KEYWORD_RULES`:

```python
_PREFIX_MAP = {
    "YOURSERVICE": "Your Category",  # matches YOURSERVICE_* tool names
    ...
}
```

### Fix a Bug

1. Reproduce the bug locally
2. Fix it
3. Verify the fix in the UI or via `curl`
4. Submit PR with "fix: description of what was broken"

## Commit Messages

Use conventional commits:
- `feat:` — new feature
- `fix:` — bug fix
- `perf:` — performance improvement
- `docs:` — documentation only
- `refactor:` — code change that neither fixes a bug nor adds a feature

## Questions?

- [Open a discussion](https://github.com/syedfahimdev/ToolsDNS/discussions/new) for ideas
- [Open an issue](https://github.com/syedfahimdev/ToolsDNS/issues/new) for bugs
- Email [hello@toolsdns.com](mailto:hello@toolsdns.com) for anything else
