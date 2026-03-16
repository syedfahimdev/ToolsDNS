# Changelog

All notable changes to ToolsDNS are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- `POST /dl/upload` endpoint — skills can upload files and get download URLs, avoiding base64 in context
- `GET /v1/system-prompt` — dynamic system prompt with live tool count for agents
- `stateless_http=True` MCP mode — no session IDs, survives server restarts cleanly
- SECURITY.md, CODE_OF_CONDUCT.md, PR template, CODEOWNERS
- MIT License (switched from AGPL-3.0)
- `develop` branch — all PRs now target `develop` before merging to `master`

### Fixed
- Filename sanitization in `/dl/upload` to prevent Content-Disposition injection
- Dev key (`td_dev_key`) now logs a clear warning when active
- `get_system_prompt` MCP tool now uses `?format=json` to avoid JSON parse error
- deploy.sh: `read -r DOMAIN` now reads from `/dev/tty` — no crash when piped via `curl | bash`
- deploy.sh: removed slow ONNX install that caused silent hangs on slow VPS
- CI branding check fixed — no longer false-positives on `ToolsDNS`

### Changed
- deploy.sh summary now shows exact copy-paste MCP config JSON, all created files, and `TOOLDNS_URL` instructions

---

## [1.0.0] — 2026-03-01

### Added
- Initial open-source release
- Semantic search over 5,000+ MCP tools (returns 1–3 results)
- Persistent MCP HTTP server (11ms vs 1.3s cold-start)
- CLI management tools for sources, keys, and token savings (`tooldns` interactive menu)
- One-command deploy: `curl ... | bash`
- API key management with per-key usage tracking
- Skills system — markdown-based agent workflows
- Marketplace: one-click install for GitHub, Gmail, Slack, Notion, 30+ servers
- Token savings tracker with per-model cost reporting
- File download store (`/dl/{token}`) — 15-min expiring URLs
- Auto-discovery from Smithery, npm, GitHub repos, OpenAPI specs
