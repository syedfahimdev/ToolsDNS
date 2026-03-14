"""
ui.py — Web UI for ToolDNS management.

Provides a browser-based dashboard at /ui for managing sources,
browsing tools, monitoring health, and adding MCP servers without
editing raw JSON config files.

Uses Jinja2 templates + HTMX for dynamic interactions.
No build step required — HTMX is loaded from CDN.
"""

import os
import re
from pathlib import Path
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

ui_router = APIRouter(prefix="/ui", tags=["ui"])

# Template directory relative to this file
_template_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_template_dir))

# Dependencies injected by main.py
_database = None
_ingestion_pipeline = None
_health_monitor = None


def init_ui(database, ingestion_pipeline, health_monitor=None):
    """Inject dependencies from main.py lifespan."""
    global _database, _ingestion_pipeline, _health_monitor
    _database = database
    _ingestion_pipeline = ingestion_pipeline
    _health_monitor = health_monitor


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@ui_router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard — overview of tools, sources, health."""
    tool_count = _database.get_tool_count()
    sources = _database.get_all_sources()
    health = _database.get_health_summary()
    cache_stats = _database.get_embedding_cache_stats()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "tool_count": tool_count,
        "source_count": len(sources),
        "sources": sources[:5],  # Show last 5 on dashboard
        "health": health,
        "cache_stats": cache_stats,
        "page": "dashboard",
    })


# ---------------------------------------------------------------------------
# Sources management
# ---------------------------------------------------------------------------

@ui_router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request, msg: str = ""):
    """Sources management page — list sources, add MCP server."""
    sources = _database.get_all_sources()
    return templates.TemplateResponse("sources.html", {
        "request": request,
        "sources": sources,
        "msg": msg,
        "page": "sources",
    })


@ui_router.post("/sources/add-mcp")
async def add_mcp_server(
    request: Request,
    name: str = Form(...),
    transport: str = Form(...),
    command: str = Form(""),
    args: str = Form(""),
    url: str = Form(""),
    headers_raw: str = Form(""),
    env_vars_raw: str = Form(""),
):
    """
    Add a new MCP server from the web form.

    Accepts a user-friendly textarea for env vars (KEY=VALUE, one per line)
    and headers, then saves them to ~/.tooldns/.env and config.json.
    """
    from tooldns.config import TOOLDNS_HOME

    # Validate name
    if not re.match(r'^[a-zA-Z0-9_\-]+$', name):
        return RedirectResponse(
            f"/ui/sources?msg=error:Name+must+only+contain+letters,+numbers,+hyphens",
            status_code=303
        )

    # Parse env vars (KEY=VALUE per line)
    env_vars = {}
    for line in env_vars_raw.strip().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            env_vars[key.strip()] = val.strip()

    # Parse headers (KEY=VALUE per line)
    headers = {}
    for line in headers_raw.strip().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            headers[key.strip()] = val.strip()

    # Parse args (space separated or one per line)
    args_list = args.strip().split() if args.strip() else []

    # Save env vars to ~/.tooldns/.env
    if env_vars:
        env_path = TOOLDNS_HOME / ".env"
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k] = v
        existing.update(env_vars)
        with open(env_path, "w") as f:
            for k, v in existing.items():
                f.write(f"{k}={v}\n")
        os.chmod(env_path, 0o600)
        # Set them in current process too
        for k, v in env_vars.items():
            os.environ[k] = v

    # Update ~/.tooldns/config.json
    config_path = TOOLDNS_HOME / "config.json"
    config_data = {}
    if config_path.exists():
        import json
        config_data = json.loads(config_path.read_text())
    if "mcpServers" not in config_data:
        config_data["mcpServers"] = {}

    server_entry = {}
    if transport == "stdio":
        server_entry["command"] = command.strip()
        server_entry["args"] = args_list
    else:
        server_entry["url"] = url.strip()
        if headers:
            server_entry["headers"] = headers

    config_data["mcpServers"][name] = server_entry

    import json
    config_path.write_text(json.dumps(config_data, indent=2))

    # Ingest immediately
    try:
        if transport == "stdio":
            source_config = {
                "type": "mcp_stdio",
                "name": name,
                "command": command.strip(),
                "args": args_list,
            }
        else:
            source_config = {
                "type": "mcp_http",
                "name": name,
                "url": url.strip(),
                "headers": headers or None,
            }
        count = _ingestion_pipeline.ingest_source(source_config)
        return RedirectResponse(
            f"/ui/sources?msg=success:Added+{name}+with+{count}+tools",
            status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            f"/ui/sources?msg=error:{str(e)[:80].replace(' ', '+')}",
            status_code=303
        )


@ui_router.post("/sources/{source_id}/refresh")
async def refresh_source(source_id: str):
    """Refresh a single source (HTMX trigger)."""
    import asyncio
    source = _database.get_source(source_id)
    if not source:
        return HTMLResponse("<span class='badge badge-error'>Not found</span>")

    try:
        config = source["config"]
        config["name"] = source["name"]
        config["type"] = source["type"]
        count = _ingestion_pipeline.ingest_source(config)
        return HTMLResponse(f"<span class='badge badge-ok'>Refreshed — {count} tools</span>")
    except Exception as e:
        return HTMLResponse(f"<span class='badge badge-error'>Error: {str(e)[:60]}</span>")


@ui_router.post("/sources/{source_id}/delete")
async def delete_source(source_id: str):
    """Delete a source (HTMX trigger — returns empty row)."""
    _database.delete_source(source_id)
    return HTMLResponse("")  # HTMX replaces row with nothing


@ui_router.post("/ingest-all")
async def ingest_all_ui():
    """Trigger full re-ingest from UI (HTMX)."""
    import asyncio, uuid
    from tooldns.api import _run_ingest_job
    job_id = str(uuid.uuid4())
    _database.create_job(job_id)
    asyncio.create_task(_run_ingest_job(job_id))
    return HTMLResponse(
        f"<span class='badge badge-ok'>Ingestion queued — job {job_id[:8]}...</span>"
    )


# ---------------------------------------------------------------------------
# Tools browser
# ---------------------------------------------------------------------------

@ui_router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request, q: str = "", source: str = ""):
    """Tools browser with live search."""
    if q:
        results = _search_engine_search(q, top_k=20)
        tools = [{
            "id": r["id"], "name": r["name"],
            "description": r["description"],
            "source": r["source"],
            "confidence": f"{r['confidence']:.0%}",
            "health_status": "unknown",
        } for r in results]
    elif source:
        raw = _database.get_tools_by_source(source)
        tools = [_tool_row(t) for t in raw]
    else:
        raw = _database.get_all_tools()
        tools = [_tool_row(t) for t in raw]

    sources = _database.get_all_sources()
    return templates.TemplateResponse("tools.html", {
        "request": request,
        "tools": tools,
        "query": q,
        "selected_source": source,
        "sources": sources,
        "total": len(tools),
        "page": "tools",
    })


@ui_router.get("/tools/search", response_class=HTMLResponse)
async def tools_search_partial(q: str = Query(""), source: str = Query("")):
    """HTMX partial: search results table body."""
    if q:
        results = _search_engine_search(q, top_k=20)
        tools = [{
            "id": r["id"], "name": r["name"],
            "description": r["description"],
            "source": r["source"],
            "confidence": f"{r['confidence']:.0%}",
        } for r in results]
    elif source:
        raw = _database.get_tools_by_source(source)
        tools = [_tool_row(t) for t in raw]
    else:
        raw = _database.get_all_tools()
        tools = [_tool_row(t) for t in raw]

    rows = ""
    for t in tools[:50]:
        conf = t.get("confidence", "")
        conf_badge = f"<span class='conf'>{conf}</span>" if conf else ""
        rows += f"""
        <tr>
            <td><code>{t['name']}</code></td>
            <td class='desc'>{t['description'][:100]}</td>
            <td><span class='src'>{t.get('source','?')}</span></td>
            <td>{conf_badge}</td>
        </tr>"""
    if not rows:
        rows = "<tr><td colspan='4' class='empty'>No tools found</td></tr>"
    return HTMLResponse(rows)


def _tool_row(t: dict) -> dict:
    si = t.get("source_info", {})
    return {
        "id": t["id"],
        "name": t["name"],
        "description": t.get("description", ""),
        "source": si.get("source_name", "?"),
        "confidence": "",
        "health_status": t.get("health_status", "unknown"),
    }


def _search_engine_search(query: str, top_k: int = 10) -> list[dict]:
    """Use the global search engine from api module."""
    from tooldns.api import _search_engine
    if _search_engine is None:
        return []
    result = _search_engine.search(query=query, top_k=top_k, threshold=0.0)
    return [r.dict() for r in result.results]


# ---------------------------------------------------------------------------
# Health monitoring
# ---------------------------------------------------------------------------

@ui_router.get("/health", response_class=HTMLResponse)
async def health_page(request: Request):
    """Health status page for all sources."""
    health = _database.get_health_summary()
    return templates.TemplateResponse("health.html", {
        "request": request,
        "health": health,
        "page": "health",
    })


@ui_router.post("/health/check")
async def trigger_health_check_ui():
    """HTMX: trigger health check and return updated status."""
    import asyncio
    if _health_monitor:
        await _health_monitor.check_all()
    health = _database.get_health_summary()
    healthy = health["healthy"]
    degraded = health["degraded"]
    down = health["down"]
    return HTMLResponse(
        f"<span class='badge badge-ok'>Check complete — "
        f"{healthy} healthy, {degraded} degraded, {down} down</span>"
    )


# ---------------------------------------------------------------------------
# Skill creator
# ---------------------------------------------------------------------------

@ui_router.get("/skills/new", response_class=HTMLResponse)
async def new_skill_page(request: Request, msg: str = ""):
    """Skill creation form."""
    return templates.TemplateResponse("new_skill.html", {
        "request": request,
        "msg": msg,
        "page": "skills",
    })


@ui_router.post("/skills/create")
async def create_skill_ui(
    name: str = Form(...),
    description: str = Form(...),
    content: str = Form(...),
):
    """Create a new skill from the web form."""
    import re as _re
    from tooldns.config import TOOLDNS_HOME
    from tooldns.models import SourceType

    if not _re.match(r'^[a-zA-Z0-9_\-]+$', name):
        return RedirectResponse(
            "/ui/skills/new?msg=error:Name+must+only+contain+letters,+numbers,+hyphens",
            status_code=303
        )

    if len(content) > 500_000:
        return RedirectResponse(
            "/ui/skills/new?msg=error:Content+too+large+(max+500KB)",
            status_code=303
        )

    skill_dir = TOOLDNS_HOME / "skills"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_folder = skill_dir / name
    skill_folder.mkdir(exist_ok=True)

    if not content.startswith("---"):
        content = f"---\nname: {name}\ndescription: {description}\n---\n\n{content}"

    (skill_folder / "SKILL.md").write_text(content, encoding="utf-8")

    try:
        count = _ingestion_pipeline.ingest_source({
            "type": SourceType.SKILL_DIRECTORY.value,
            "name": f"skills-{name}",
            "path": str(skill_dir),
        })
        return RedirectResponse(
            f"/ui/sources?msg=success:Skill+{name}+created+and+indexed+({count}+tools)",
            status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            f"/ui/skills/new?msg=error:{str(e)[:80].replace(' ', '+')}",
            status_code=303
        )


# ---------------------------------------------------------------------------
# Token savings statistics
# ---------------------------------------------------------------------------

@ui_router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    """Detailed token savings and cost analytics page."""
    from tooldns.tokens import MODEL_PRICES, get_model_price
    import os

    stats = _database.get_search_stats()
    current_model = os.environ.get("TOOLDNS_MODEL", "")
    if not current_model:
        try:
            import json
            cfg = json.load(open(os.path.expanduser("~/.nanobot/config.json")))
            current_model = cfg.get("model", "") or (
                (cfg.get("agents", {}).get("defaults") or {}).get("model", "")
            )
        except Exception:
            pass

    current_price = get_model_price(current_model) if current_model else None

    # Compute "what-if" costs for all known models based on total tokens saved
    total_saved = stats["total_tokens_saved"]
    what_if = []
    for key, price in MODEL_PRICES.items():
        what_if.append({
            "model": key,
            "price_per_million": price,
            "cost_saved": round((total_saved / 1_000_000) * price, 6),
        })
    what_if.sort(key=lambda x: x["price_per_million"], reverse=True)

    return templates.TemplateResponse("stats.html", {
        "request": request,
        "stats": stats,
        "current_model": current_model,
        "current_price": current_price,
        "what_if": what_if,
        "page": "stats",
    })


@ui_router.get("/stats/model-update", response_class=HTMLResponse)
async def update_model_partial(model: str = Query("")):
    """HTMX partial: recalculate savings for a selected model."""
    from tooldns.tokens import get_model_price, tokens_to_cost
    stats = _database.get_search_stats()
    total_saved = stats["total_tokens_saved"]
    price = get_model_price(model) if model else None

    if price is None:
        return HTMLResponse(
            "<span class='no-data'>Unknown model — price not available</span>"
        )

    cost = tokens_to_cost(total_saved, price)
    avg_cost = tokens_to_cost(stats["avg_tokens_saved"], price)

    return HTMLResponse(f"""
    <div class="model-result">
      <div class="model-stat">
        <span class="model-stat-num">${cost:.4f}</span>
        <span class="model-stat-label">Total saved ({stats['total_searches']} searches)</span>
      </div>
      <div class="model-stat">
        <span class="model-stat-num">${avg_cost:.6f}</span>
        <span class="model-stat-label">Saved per search</span>
      </div>
      <div class="model-stat">
        <span class="model-stat-num">${price:.2f}</span>
        <span class="model-stat-label">Per 1M input tokens</span>
      </div>
    </div>
    """)


@ui_router.post("/stats/set-model")
async def set_model(model: str = Form(...)):
    """Save TOOLDNS_MODEL to .env so cost calculations work."""
    from tooldns.config import TOOLDNS_HOME
    env_path = TOOLDNS_HOME / ".env"
    lines = []
    found = False
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("TOOLDNS_MODEL="):
                lines.append(f"TOOLDNS_MODEL={model.strip()}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"TOOLDNS_MODEL={model.strip()}")
    env_path.write_text("\n".join(lines) + "\n")
    os.chmod(env_path, 0o600)
    os.environ["TOOLDNS_MODEL"] = model.strip()
    return RedirectResponse(f"/ui/stats?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Source editing
# ---------------------------------------------------------------------------

@ui_router.get("/sources/{source_id}/edit", response_class=HTMLResponse)
async def edit_source_page(request: Request, source_id: str, msg: str = ""):
    """Edit a source's connection config."""
    source = _database.get_source(source_id)
    if not source:
        return RedirectResponse("/ui/sources?msg=error:Source+not+found", status_code=303)
    return templates.TemplateResponse("edit_source.html", {
        "request": request,
        "source": source,
        "msg": msg,
        "page": "sources",
    })


@ui_router.post("/sources/{source_id}/edit")
async def save_source_edit(
    source_id: str,
    command: str = Form(""),
    args: str = Form(""),
    url: str = Form(""),
    headers_raw: str = Form(""),
    path: str = Form(""),
    config_key: str = Form("mcpServers"),
    env_vars_raw: str = Form(""),
):
    """Save edited source config and re-ingest."""
    from tooldns.config import TOOLDNS_HOME

    source = _database.get_source(source_id)
    if not source:
        return RedirectResponse("/ui/sources?msg=error:Source+not+found", status_code=303)

    # Save new env vars
    if env_vars_raw.strip():
        env_path = TOOLDNS_HOME / ".env"
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k] = v
        for line in env_vars_raw.strip().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
        with open(env_path, "w") as f:
            for k, v in existing.items():
                f.write(f"{k}={v}\n")
        os.chmod(env_path, 0o600)

    # Build updated config
    config = dict(source["config"])
    stype = source["type"]
    if stype == "mcp_stdio":
        config["command"] = command.strip()
        config["args"] = args.strip().split() if args.strip() else []
    elif stype == "mcp_http":
        config["url"] = url.strip()
        if headers_raw.strip():
            hdrs = {}
            for line in headers_raw.strip().splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    hdrs[k.strip()] = v.strip()
            config["headers"] = hdrs
    elif stype == "mcp_config":
        config["path"] = path.strip()
        config["config_key"] = config_key.strip()

    config["name"] = source["name"]
    config["type"] = stype

    try:
        count = _ingestion_pipeline.ingest_source(config)
        return RedirectResponse(
            f"/ui/sources?msg=success:Updated+{source['name']}+({count}+tools)",
            status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            f"/ui/sources/{source_id}/edit?msg=error:{str(e)[:80].replace(' ', '+')}",
            status_code=303
        )


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

def _detect_model() -> tuple[str, str]:
    """
    Auto-detect the active LLM model.

    Returns (model_name, source) — source is a human-readable label.
    Skips aliases like 'auto-fastest' that don't map to real model IDs.
    """
    import json as _json

    SKIP_ALIASES = {"auto-fastest", "auto", "default", "latest", "fastest"}

    # 1. Explicit env override
    m = os.environ.get("TOOLDNS_MODEL", "").strip()
    if m and m.lower() not in SKIP_ALIASES:
        return m, "TOOLDNS_MODEL env var"

    # 2. Nanobot config (~/.nanobot/config.json)
    try:
        cfg = _json.load(open(os.path.expanduser("~/.nanobot/config.json")))
        m = cfg.get("model", "") or ((cfg.get("agents", {}).get("defaults") or {}).get("model", ""))
        if m and m.lower() not in SKIP_ALIASES:
            return m, "~/.nanobot/config.json"
    except Exception:
        pass

    # 3. OpenClaw config — use first anthropic model listed
    try:
        for cfg_path in [
            os.path.expanduser("~/.openclaw/openclaw.json"),
            os.path.expanduser("~/.openclaw/workspace/openclaw.json"),
        ]:
            if not os.path.exists(cfg_path):
                continue
            cfg = _json.load(open(cfg_path))
            providers = cfg.get("models", {}).get("providers", {})
            for provider_name, provider in providers.items():
                for model_entry in provider.get("models", []):
                    mid = model_entry.get("id", "")
                    if mid and mid.lower() not in SKIP_ALIASES:
                        return mid, f"openclaw ({provider_name})"
    except Exception:
        pass

    return "", ""


def _read_env_file() -> tuple[dict, str]:
    """Read ~/.tooldns/.env and return (dict, raw_text)."""
    from tooldns.config import TOOLDNS_HOME
    env_path = TOOLDNS_HOME / ".env"
    if not env_path.exists():
        return {}, ""
    raw = env_path.read_text()
    env = {}
    for line in raw.splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env, raw


def _save_env_key(key: str, value: str):
    """Update a single key in ~/.tooldns/.env."""
    from tooldns.config import TOOLDNS_HOME
    env_path = TOOLDNS_HOME / ".env"
    env, _ = _read_env_file()
    env[key] = value
    with open(env_path, "w") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")
    os.chmod(env_path, 0o600)
    os.environ[key] = value


@ui_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, msg: str = ""):
    """Settings — model, API key, env vars, framework imports."""
    env, env_raw = _read_env_file()
    detected_model, detected_source = _detect_model()

    # Detect framework configs present on this machine
    framework_configs = []
    candidates = [
        ("~/.nanobot/config.json", "mcpServers", "Nanobot"),
        ("~/.openclaw/workspace/config/mcporter.json", "mcpServers", "OpenClaw (mcporter)"),
        ("~/.tooldns/config.json", "mcpServers", "ToolDNS local"),
    ]
    for path_str, key, label in candidates:
        path = os.path.expanduser(path_str)
        if os.path.exists(path):
            framework_configs.append({"path": path, "key": key, "label": label})

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "env": env,
        "env_raw": env_raw,
        "detected_model": detected_model,
        "detected_source": detected_source,
        "framework_configs": framework_configs,
        "msg": msg,
        "page": "settings",
    })


@ui_router.post("/settings/save")
async def settings_save(
    model: str = Form(""),
    api_key: str = Form(""),
):
    """Save model and/or API key to .env."""
    if model.strip():
        _save_env_key("TOOLDNS_MODEL", model.strip())
    if api_key.strip():
        _save_env_key("TOOLDNS_API_KEY", api_key.strip())
    return RedirectResponse("/ui/settings?msg=success:Settings+saved", status_code=303)


@ui_router.post("/settings/save-env")
async def settings_save_env(env_raw: str = Form(...)):
    """Overwrite ~/.tooldns/.env with edited content."""
    from tooldns.config import TOOLDNS_HOME
    env_path = TOOLDNS_HOME / ".env"
    env_path.write_text(env_raw)
    os.chmod(env_path, 0o600)
    # Reload into current process
    for line in env_raw.splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()
    return RedirectResponse("/ui/settings?msg=success:Environment+variables+saved", status_code=303)


@ui_router.post("/settings/import-config")
async def settings_import_config(
    config_path: str = Form(...),
    config_key: str = Form("mcpServers"),
):
    """Import MCP servers from a framework config file."""
    import uuid, asyncio
    from tooldns.api import _run_ingest_job
    from tooldns.models import SourceType

    path = os.path.expanduser(config_path.strip())
    if not os.path.exists(path):
        return RedirectResponse(
            f"/ui/settings?msg=error:File+not+found:+{config_path[:60].replace(' ', '+')}",
            status_code=303
        )

    source_config = {
        "type": SourceType.MCP_CONFIG.value,
        "name": f"import-{os.path.basename(os.path.dirname(path))}",
        "path": path,
        "config_key": config_key,
    }
    try:
        count = _ingestion_pipeline.ingest_source(source_config)
        return RedirectResponse(
            f"/ui/settings?msg=success:Imported+{count}+tools+from+{os.path.basename(path)}",
            status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            f"/ui/settings?msg=error:{str(e)[:80].replace(' ', '+')}",
            status_code=303
        )


@ui_router.post("/settings/clear-cache")
async def settings_clear_cache():
    """Clear the embedding cache."""
    _database.clear_embedding_cache()
    return RedirectResponse("/ui/settings?msg=success:Embedding+cache+cleared", status_code=303)


@ui_router.post("/settings/clear-stats")
async def settings_clear_stats():
    """Delete all search_log entries."""
    import sqlite3
    conn = _database._get_conn()
    conn.execute("DELETE FROM search_log")
    conn.commit()
    conn.close()
    return RedirectResponse("/ui/settings?msg=success:Search+stats+cleared", status_code=303)


@ui_router.post("/settings/delete-all-sources")
async def settings_delete_all_sources():
    """Delete all sources and their tools."""
    conn = _database._get_conn()
    conn.execute("DELETE FROM sources")
    conn.execute("DELETE FROM tools")
    conn.commit()
    conn.close()
    return RedirectResponse("/ui/settings?msg=success:All+sources+and+tools+deleted", status_code=303)


# ---------------------------------------------------------------------------
# API Key Manager (SaaS / multi-tenant)
# ---------------------------------------------------------------------------

@ui_router.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request, msg: str = ""):
    """API key management page — create, view, revoke customer keys."""
    from tooldns.config import settings
    keys = _database.get_all_api_keys()
    stats = _database.get_search_stats()
    return templates.TemplateResponse("keys.html", {
        "request": request,
        "keys": keys,
        "admin_key": settings.api_key,
        "msg": msg,
        "total_searches": stats.get("total_searches", 0),
        "page": "keys",
    })


@ui_router.post("/keys/create")
async def create_key(
    name: str = Form(...),
    label: str = Form(""),
    plan: str = Form("free"),
    monthly_limit: int = Form(0),
):
    """Create a new named API key."""
    if not name.strip():
        return RedirectResponse("/ui/keys?msg=error:Name+is+required", status_code=303)
    key = _database.create_api_key(
        name=name.strip(),
        label=label.strip(),
        plan=plan.strip(),
        monthly_limit=monthly_limit,
    )
    # URL-encode the key for the msg param
    safe_key = key.replace("_", "%5F")
    return RedirectResponse(f"/ui/keys?msg=success:Key+created:+{key}", status_code=303)


@ui_router.post("/keys/{key}/revoke")
async def revoke_key(key: str):
    """Revoke (deactivate) a key — HTMX."""
    _database.revoke_api_key(key)
    return HTMLResponse("<span class='badge badge-warn'>Revoked</span>")


@ui_router.post("/keys/{key}/delete")
async def delete_key_ui(key: str):
    """Permanently delete a key — HTMX."""
    _database.delete_api_key(key)
    return HTMLResponse("")


@ui_router.post("/keys/{key}/reset")
async def reset_key_count(key: str):
    """Reset a key's monthly search count — HTMX."""
    _database.reset_key_monthly_count(key)
    return HTMLResponse("<span class='badge badge-ok'>Reset</span>")


# ---------------------------------------------------------------------------
# Pricing page
# ---------------------------------------------------------------------------

@ui_router.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    """Public-facing pricing page for selling ToolDNS as a service."""
    return templates.TemplateResponse("pricing.html", {
        "request": request,
        "page": "pricing",
    })


# ---------------------------------------------------------------------------
# Shareable Savings Card
# ---------------------------------------------------------------------------

@ui_router.get("/savings-card", response_class=HTMLResponse)
async def savings_card(request: Request):
    """Shareable token savings card — screenshot this and post it."""
    stats = _database.get_search_stats()
    tool_count = _database.get_tool_count()
    source_count = len(_database.get_all_sources())

    tokens_saved = stats.get("total_tokens_saved") or 0
    cost_saved = stats.get("total_cost_saved_usd") or 0.0
    total_searches = stats.get("total_searches") or 0

    def _fmt(n):
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(n)

    tokens_fmt = _fmt(tokens_saved)
    cost_fmt = f"${cost_saved:.2f}"
    searches_fmt = _fmt(total_searches)
    tools_fmt = _fmt(tool_count)

    card_url = str(request.url_for("savings_card"))
    svg_url = str(request.url_for("savings_card_svg"))

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ToolDNS — Token Savings</title>
<meta property="og:title" content="I saved {tokens_fmt} tokens with ToolDNS">
<meta property="og:description" content="DNS for AI Tools — semantic search over 10,000+ tools so your LLM only sees what it needs.">
<meta property="og:image" content="{svg_url}">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0f172a; font-family:'Inter',system-ui,sans-serif; min-height:100vh;
          display:flex; flex-direction:column; align-items:center; justify-content:center;
          padding:24px; color:#f8fafc; }}
  .card {{
    background:linear-gradient(135deg,#1e293b 0%,#0f172a 50%,#1a2744 100%);
    border:1px solid #334155; border-radius:20px;
    padding:48px 56px; max-width:560px; width:100%;
    box-shadow:0 25px 60px rgba(0,0,0,0.5);
    position:relative; overflow:hidden;
  }}
  .card::before {{
    content:''; position:absolute; top:-60px; right:-60px;
    width:220px; height:220px; border-radius:50%;
    background:radial-gradient(circle,rgba(99,102,241,0.15) 0%,transparent 70%);
  }}
  .logo {{ display:flex; align-items:center; gap:10px; margin-bottom:32px; }}
  .logo-icon {{ width:32px; height:32px; background:linear-gradient(135deg,#6366f1,#8b5cf6);
                border-radius:8px; display:flex; align-items:center; justify-content:center;
                font-size:18px; }}
  .logo-text {{ font-size:18px; font-weight:700; color:#e2e8f0; }}
  .logo-sub {{ font-size:12px; color:#64748b; margin-top:1px; }}
  .tagline {{ font-size:13px; color:#64748b; margin-bottom:36px; }}
  .stats {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:36px; }}
  .stat {{ background:rgba(255,255,255,0.03); border:1px solid #1e3a5f;
           border-radius:12px; padding:20px; }}
  .stat-num {{ font-size:36px; font-weight:800; background:linear-gradient(135deg,#6366f1,#a78bfa);
               -webkit-background-clip:text; -webkit-text-fill-color:transparent;
               background-clip:text; line-height:1; }}
  .stat-num.green {{ background:linear-gradient(135deg,#10b981,#34d399);
                     -webkit-background-clip:text; -webkit-text-fill-color:transparent;
                     background-clip:text; }}
  .stat-label {{ font-size:12px; color:#64748b; margin-top:6px; text-transform:uppercase;
                 letter-spacing:0.05em; }}
  .divider {{ height:1px; background:linear-gradient(90deg,transparent,#334155,transparent);
              margin:28px 0; }}
  .footer {{ display:flex; align-items:center; justify-content:space-between; }}
  .badge {{ font-size:12px; color:#64748b; }}
  .badge strong {{ color:#94a3b8; }}
  .actions {{ display:flex; gap:10px; margin-top:32px; }}
  .btn {{ padding:10px 20px; border-radius:8px; font-size:13px; font-weight:600;
          cursor:pointer; border:none; text-decoration:none; display:inline-flex;
          align-items:center; gap:6px; }}
  .btn-primary {{ background:linear-gradient(135deg,#6366f1,#8b5cf6); color:#fff; }}
  .btn-secondary {{ background:#1e293b; border:1px solid #334155; color:#94a3b8; }}
  .btn:hover {{ opacity:0.85; }}
  .copy-hint {{ font-size:11px; color:#475569; margin-top:12px; text-align:center; }}
  @media(max-width:500px) {{
    .card {{ padding:32px 24px; }}
    .stats {{ grid-template-columns:1fr; }}
    .stat-num {{ font-size:28px; }}
  }}
</style>
</head>
<body>
  <div class="card" id="savings-card">
    <div class="logo">
      <div class="logo-icon">⚡</div>
      <div>
        <div class="logo-text">ToolDNS</div>
        <div class="logo-sub">DNS for AI Tools</div>
      </div>
    </div>
    <div class="tagline">Semantic search over {tools_fmt} tools — LLMs only see what they need</div>

    <div class="stats">
      <div class="stat">
        <div class="stat-num">{tokens_fmt}</div>
        <div class="stat-label">Tokens Saved</div>
      </div>
      <div class="stat">
        <div class="stat-num green">{cost_fmt}</div>
        <div class="stat-label">Cost Saved</div>
      </div>
      <div class="stat">
        <div class="stat-num">{searches_fmt}</div>
        <div class="stat-label">Searches</div>
      </div>
      <div class="stat">
        <div class="stat-num">{tools_fmt}</div>
        <div class="stat-label">Tools Indexed</div>
      </div>
    </div>

    <div class="divider"></div>
    <div class="footer">
      <div class="badge">
        <strong>{source_count}</strong> sources registered
      </div>
      <div class="badge">github.com/syedfahimdev/tooldns</div>
    </div>
  </div>

  <div class="actions">
    <button class="btn btn-primary" onclick="copyLink()">🔗 Copy Link</button>
    <a href="{svg_url}" class="btn btn-secondary" download="tooldns-savings.svg">⬇ Download SVG</a>
    <a href="/ui/stats" class="btn btn-secondary">📊 Full Stats</a>
  </div>
  <div class="copy-hint" id="copy-hint">&nbsp;</div>

<script>
function copyLink() {{
  navigator.clipboard.writeText(window.location.href).then(() => {{
    document.getElementById('copy-hint').textContent = '✓ Link copied to clipboard!';
    setTimeout(() => document.getElementById('copy-hint').textContent = '\\u00a0', 2000);
  }});
}}
</script>
</body>
</html>""")


@ui_router.get("/savings-card.svg")
async def savings_card_svg(request: Request):
    """SVG image version of savings card — embeddable in README/tweets."""
    from fastapi.responses import Response
    stats = _database.get_search_stats()
    tool_count = _database.get_tool_count()

    tokens_saved = stats.get("total_tokens_saved") or 0
    cost_saved = stats.get("total_cost_saved_usd") or 0.0
    total_searches = stats.get("total_searches") or 0

    def _fmt(n):
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(int(n))

    tokens_fmt = _fmt(tokens_saved)
    cost_fmt = f"${cost_saved:.2f}"
    searches_fmt = _fmt(total_searches)
    tools_fmt = _fmt(tool_count)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="560" height="280" viewBox="0 0 560 280">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1e293b"/>
      <stop offset="100%" style="stop-color:#0f172a"/>
    </linearGradient>
    <linearGradient id="accent" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#6366f1"/>
      <stop offset="100%" style="stop-color:#a78bfa"/>
    </linearGradient>
    <linearGradient id="green" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#10b981"/>
      <stop offset="100%" style="stop-color:#34d399"/>
    </linearGradient>
    <clipPath id="rounded"><rect width="560" height="280" rx="16"/></clipPath>
  </defs>
  <rect width="560" height="280" rx="16" fill="url(#bg)" stroke="#334155" stroke-width="1"/>
  <circle cx="480" cy="-20" r="140" fill="rgba(99,102,241,0.08)"/>

  <!-- Logo -->
  <rect x="24" y="24" width="32" height="32" rx="8" fill="url(#accent)"/>
  <text x="40" y="45" font-family="system-ui" font-size="18" fill="white" text-anchor="middle">⚡</text>
  <text x="64" y="37" font-family="system-ui,sans-serif" font-size="16" font-weight="700" fill="#e2e8f0">ToolDNS</text>
  <text x="64" y="52" font-family="system-ui,sans-serif" font-size="11" fill="#64748b">DNS for AI Tools</text>

  <!-- Divider -->
  <line x1="24" y1="70" x2="536" y2="70" stroke="#1e3a5f" stroke-width="1"/>

  <!-- Stat boxes -->
  <!-- Tokens Saved -->
  <rect x="24" y="84" width="120" height="80" rx="10" fill="rgba(255,255,255,0.03)" stroke="#1e3a5f" stroke-width="1"/>
  <text x="84" y="124" font-family="system-ui,sans-serif" font-size="26" font-weight="800" fill="url(#accent)" text-anchor="middle">{tokens_fmt}</text>
  <text x="84" y="148" font-family="system-ui,sans-serif" font-size="10" fill="#64748b" text-anchor="middle" letter-spacing="1">TOKENS SAVED</text>

  <!-- Cost Saved -->
  <rect x="156" y="84" width="120" height="80" rx="10" fill="rgba(255,255,255,0.03)" stroke="#1e3a5f" stroke-width="1"/>
  <text x="216" y="124" font-family="system-ui,sans-serif" font-size="26" font-weight="800" fill="url(#green)" text-anchor="middle">{cost_fmt}</text>
  <text x="216" y="148" font-family="system-ui,sans-serif" font-size="10" fill="#64748b" text-anchor="middle" letter-spacing="1">COST SAVED</text>

  <!-- Searches -->
  <rect x="288" y="84" width="120" height="80" rx="10" fill="rgba(255,255,255,0.03)" stroke="#1e3a5f" stroke-width="1"/>
  <text x="348" y="124" font-family="system-ui,sans-serif" font-size="26" font-weight="800" fill="url(#accent)" text-anchor="middle">{searches_fmt}</text>
  <text x="348" y="148" font-family="system-ui,sans-serif" font-size="10" fill="#64748b" text-anchor="middle" letter-spacing="1">SEARCHES</text>

  <!-- Tools Indexed -->
  <rect x="420" y="84" width="116" height="80" rx="10" fill="rgba(255,255,255,0.03)" stroke="#1e3a5f" stroke-width="1"/>
  <text x="478" y="124" font-family="system-ui,sans-serif" font-size="26" font-weight="800" fill="url(#accent)" text-anchor="middle">{tools_fmt}</text>
  <text x="478" y="148" font-family="system-ui,sans-serif" font-size="10" fill="#64748b" text-anchor="middle" letter-spacing="1">TOOLS INDEXED</text>

  <!-- Tagline -->
  <text x="24" y="196" font-family="system-ui,sans-serif" font-size="13" fill="#94a3b8">Semantic search so LLMs only see what they need — not 500 schemas</text>

  <!-- Bottom divider -->
  <line x1="24" y1="212" x2="536" y2="212" stroke="#1e3a5f" stroke-width="1"/>

  <!-- Footer -->
  <text x="24" y="250" font-family="system-ui,sans-serif" font-size="11" fill="#475569">github.com/syedfahimdev/tooldns</text>
  <rect x="390" y="232" width="146" height="26" rx="6" fill="url(#accent)" opacity="0.15"/>
  <text x="463" y="249" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#a78bfa" text-anchor="middle">⭐ Star on GitHub</text>
</svg>"""

    return Response(content=svg, media_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Discover from URL
# ---------------------------------------------------------------------------

@ui_router.post("/sources/discover", response_class=HTMLResponse)
async def discover_source_ui(url: str = Form(...)):
    """HTMX endpoint: auto-detect source from URL and return a pre-filled form."""
    from tooldns.discover import discover_from_url

    url = url.strip()
    if not url:
        return HTMLResponse("<span class='badge badge-error'>Please enter a URL</span>")

    result = discover_from_url(url)
    if "error" in result:
        return HTMLResponse(f"<div class='alert alert-error'>{result['error']}</div>")

    cfg = result.get("source_config", {})
    name = cfg.get("name", "")
    transport = cfg.get("type", "mcp_stdio")
    command = cfg.get("command", "")
    args = " ".join(cfg.get("args", []))
    url_val = cfg.get("url", "")
    detected = result.get("detected_type", "")
    message = result.get("message", "")

    transport_type = "http" if "http" in transport else "stdio"
    stdio_display = "block" if transport_type == "stdio" else "none"
    http_display = "none" if transport_type == "stdio" else "block"

    return HTMLResponse(f"""
    <div class="alert alert-ok" style="margin-bottom:12px">
      <strong>{detected}</strong> — {message}
    </div>
    <form method="post" action="/ui/sources/add-mcp" class="mcp-form">
      <div class="form-row">
        <label>Server Name</label>
        <input type="text" name="name" value="{name}" required
               pattern="[a-zA-Z0-9_\\-]+" class="form-input">
      </div>
      <input type="hidden" name="transport" value="{transport_type}">
      <div id="discovered-stdio" style="display:{stdio_display}">
        <div class="form-row">
          <label>Command</label>
          <input type="text" name="command" value="{command}" class="form-input">
        </div>
        <div class="form-row">
          <label>Arguments</label>
          <input type="text" name="args" value="{args}" class="form-input code-input">
        </div>
      </div>
      <div id="discovered-http" style="display:{http_display}">
        <div class="form-row">
          <label>Server URL</label>
          <input type="url" name="url" value="{url_val}" class="form-input">
        </div>
      </div>
      <div class="form-row">
        <button type="submit" class="btn btn-primary">Add &amp; Index This Server</button>
        <button type="button" class="btn btn-sm" onclick="this.closest('.discover-result').remove()">Cancel</button>
      </div>
    </form>""")


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------

@ui_router.get("/marketplace", response_class=HTMLResponse)
async def marketplace_page(request: Request, category: str = "All", q: str = ""):
    """MCP & Skills Marketplace — browse and one-click install."""
    from tooldns.marketplace import MCP_SERVERS, SKILLS, CATEGORIES

    installed_ids = {s["name"] for s in _database.get_all_sources()}

    servers = MCP_SERVERS
    skills = SKILLS

    # Category filter
    if category != "All" and category != "Skills":
        servers = [s for s in servers if s["category"] == category]
        skills = []
    elif category == "Skills":
        servers = []

    # Text search
    if q:
        ql = q.lower()
        servers = [s for s in servers if ql in s["name"].lower() or ql in s["description"].lower() or ql in s["category"].lower()]
        skills = [s for s in skills if ql in s["name"].lower() or ql in s["description"].lower()]

    return templates.TemplateResponse("marketplace.html", {
        "request": request,
        "servers": servers,
        "skills": skills,
        "installed_ids": installed_ids,
        "category": category,
        "categories": CATEGORIES,
        "query": q,
        "page": "marketplace",
    })


@ui_router.get("/marketplace/install-form/{server_id}", response_class=HTMLResponse)
async def marketplace_install_form(server_id: str):
    """HTMX: return inline install form for a marketplace server."""
    from tooldns.marketplace import get_server
    server = get_server(server_id)
    if not server:
        return HTMLResponse("<span class='badge badge-error'>Server not found</span>")

    env_vars = server.get("env_vars", {})
    env_inputs = ""
    for key, default_val in env_vars.items():
        placeholder = f"Your {key}" if not default_val else default_val
        env_inputs += f"""
        <div class="form-row">
          <label>{key}</label>
          <input type="text" name="env_{key}" placeholder="{placeholder}" class="form-input">
        </div>"""

    args_val = " ".join(server.get("args", []))
    note = server.get("install_note", "")

    return HTMLResponse(f"""
    <form class="install-form" hx-post="/ui/marketplace/install" hx-target="closest .mkt-card-actions" hx-swap="innerHTML">
      <input type="hidden" name="server_id" value="{server_id}">
      {env_inputs}
      <div class="form-row">
        <label>Command args <span class="hint">(edit if needed)</span></label>
        <input type="text" name="args_override" value="{args_val}" class="form-input code-input">
      </div>
      {"<p class='install-note'>" + note + "</p>" if note else ""}
      <div class="install-form-actions">
        <button type="submit" class="btn btn-primary btn-sm">Install</button>
        <button type="button" class="btn btn-sm" onclick="this.closest('form').remove()">Cancel</button>
      </div>
    </form>""")


@ui_router.post("/marketplace/install", response_class=HTMLResponse)
async def marketplace_install(request: Request):
    """Install a marketplace server — called from the inline install form."""
    from tooldns.marketplace import get_server
    form = await request.form()
    server_id = form.get("server_id", "")
    server = get_server(server_id)
    if not server:
        return HTMLResponse("<span class='badge badge-error'>Unknown server</span>")

    # Collect env vars from form (prefixed with env_)
    env_vars = {}
    for key in form:
        if key.startswith("env_"):
            val = str(form[key]).strip()
            if val:
                env_vars[key[4:]] = val

    # Args override
    args_override = str(form.get("args_override", "")).strip()
    args = args_override.split() if args_override else server.get("args", [])

    # Save env vars
    if env_vars:
        from tooldns.config import TOOLDNS_HOME
        env_path = TOOLDNS_HOME / ".env"
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k] = v
        existing.update(env_vars)
        with open(env_path, "w") as f:
            for k, v in existing.items():
                f.write(f"{k}={v}\n")
        os.chmod(env_path, 0o600)
        for k, v in env_vars.items():
            os.environ[k] = v

    # Register and ingest
    try:
        transport = server.get("transport", "stdio")
        if transport == "stdio":
            source_config = {
                "type": "mcp_stdio",
                "name": server_id,
                "command": server["command"],
                "args": args,
            }
        else:
            source_config = {
                "type": "mcp_http",
                "name": server_id,
                "url": server.get("url", ""),
            }
        count = _ingestion_pipeline.ingest_source(source_config)
        return HTMLResponse(
            f"<span class='badge badge-ok'>✓ Installed — {count} tools indexed</span>"
        )
    except Exception as e:
        return HTMLResponse(
            f"<span class='badge badge-error'>Error: {str(e)[:80]}</span>"
        )


@ui_router.post("/marketplace/install-quick", response_class=HTMLResponse)
async def marketplace_install_quick(server_id: str = Form(...)):
    """Install a marketplace server that needs no env vars."""
    from tooldns.marketplace import get_server
    server = get_server(server_id)
    if not server:
        return HTMLResponse("<span class='badge badge-error'>Unknown server</span>")

    try:
        transport = server.get("transport", "stdio")
        if transport == "stdio":
            source_config = {
                "type": "mcp_stdio",
                "name": server_id,
                "command": server["command"],
                "args": server.get("args", []),
            }
        else:
            source_config = {
                "type": "mcp_http",
                "name": server_id,
                "url": server.get("url", ""),
            }
        count = _ingestion_pipeline.ingest_source(source_config)
        return HTMLResponse(
            f"<span class='badge badge-ok'>✓ Installed — {count} tools indexed</span>"
        )
    except Exception as e:
        return HTMLResponse(
            f"<span class='badge badge-error'>Error: {str(e)[:80]}</span>"
        )


@ui_router.post("/marketplace/install-skill", response_class=HTMLResponse)
async def marketplace_install_skill(skill_id: str = Form(...)):
    """Install a pre-built skill from the marketplace."""
    from tooldns.marketplace import get_skill
    from tooldns.config import TOOLDNS_HOME
    from tooldns.models import SourceType

    skill = get_skill(skill_id)
    if not skill:
        return HTMLResponse("<span class='badge badge-error'>Skill not found</span>")

    skill_dir = TOOLDNS_HOME / "skills"
    skill_folder = skill_dir / skill_id
    skill_folder.mkdir(parents=True, exist_ok=True)
    (skill_folder / "SKILL.md").write_text(skill["content"], encoding="utf-8")

    try:
        count = _ingestion_pipeline.ingest_source({
            "type": SourceType.SKILL_DIRECTORY.value,
            "name": f"skills-{skill_id}",
            "path": str(skill_dir),
        })
        return HTMLResponse(
            f"<span class='badge badge-ok'>✓ Installed — {count} tools indexed</span>"
        )
    except Exception as e:
        return HTMLResponse(
            f"<span class='badge badge-error'>Error: {str(e)[:80]}</span>"
        )


@ui_router.get("/marketplace/search", response_class=HTMLResponse)
async def marketplace_search(q: str = Query(""), category: str = Query("All")):
    """HTMX partial: returns just the marketplace grid HTML for search/filter."""
    from tooldns.marketplace import MCP_SERVERS, SKILLS

    installed_ids = {s["name"] for s in _database.get_all_sources()}

    servers = MCP_SERVERS
    skills = SKILLS

    if category != "All" and category != "Skills":
        servers = [s for s in servers if s["category"] == category]
        skills = []
    elif category == "Skills":
        servers = []

    if q:
        ql = q.lower()
        servers = [s for s in servers if ql in s["name"].lower() or ql in s["description"].lower()]
        skills = [s for s in skills if ql in s["name"].lower() or ql in s["description"].lower()]

    # Build minimal grid HTML
    def _server_card(s):
        installed = s["id"] in installed_ids
        installed_badge = "<span class='badge-installed'>✓ Installed</span>" if installed else ""
        popular_badge = "<span class='badge-popular'>⭐ Popular</span>" if s.get("popular") else ""
        if s.get("env_vars"):
            action = f"""<button class="btn btn-primary btn-sm"
                hx-get="/ui/marketplace/install-form/{s['id']}"
                hx-target="#install-area-{s['id']}"
                hx-swap="innerHTML">⚙ Configure &amp; Install</button>"""
        else:
            action = f"""<button class="btn btn-primary btn-sm"
                hx-post="/ui/marketplace/install-quick"
                hx-target="#install-area-{s['id']}"
                hx-swap="innerHTML"
                hx-vals='{{"server_id": "{s['id']}"}}'
                >⚡ Install</button>"""
        note = f"<div class='mkt-install-note has-env'>🔑 {s['install_note']}</div>" if s.get("install_note") else ""
        pkg = f"<code style='font-size:11px'>{s['package']}</code>" if s.get("package") else ""
        return f"""
        <div class="mkt-card" id="mkt-card-{s['id']}">
          <div class="mkt-card-header">
            <div class="mkt-card-title-row">
              <span class="mkt-icon">{s['icon']}</span>
              <span class="mkt-name">{s['name']}</span>
            </div>
            <div class="mkt-card-badges">{popular_badge}{installed_badge}</div>
          </div>
          <p class="mkt-description">{s['description']}</p>
          <div class="mkt-meta">
            <span class="badge-category">{s['category']}</span>
            <span class="badge-transport">{s['transport']}</span>
            {pkg}
          </div>
          {note}
          <div class="mkt-actions">{action}</div>
          <div class="install-area" id="install-area-{s['id']}"></div>
        </div>"""

    def _skill_card(s):
        installed = f"skills-{s['id']}" in installed_ids
        installed_badge = "<span class='badge-installed'>✓ Installed</span>" if installed else ""
        return f"""
        <div class="mkt-card">
          <div class="mkt-card-header">
            <div class="mkt-card-title-row">
              <span class="mkt-icon">{s['icon']}</span>
              <span class="mkt-name">{s['name']}</span>
            </div>
            <div class="mkt-card-badges"><span class="badge-skill">Skill</span>{installed_badge}</div>
          </div>
          <p class="mkt-description">{s['description']}</p>
          <div class="mkt-actions">
            <button class="btn btn-primary btn-sm"
              hx-post="/ui/marketplace/install-skill"
              hx-target="#install-area-skill-{s['id']}"
              hx-swap="innerHTML"
              hx-vals='{{"skill_id": "{s['id']}"}}'>📥 Install Skill</button>
          </div>
          <div class="install-area" id="install-area-skill-{s['id']}"></div>
        </div>"""

    html = ""
    if servers:
        html += f"<div class='marketplace-section-title'>MCP Servers ({len(servers)})</div>"
        html += "<div class='marketplace-grid'>" + "".join(_server_card(s) for s in servers) + "</div>"
    if skills:
        html += f"<div class='marketplace-section-title'>Pre-built Skills ({len(skills)})</div>"
        html += "<div class='marketplace-grid'>" + "".join(_skill_card(s) for s in skills) + "</div>"
    if not html:
        html = "<div class='empty-state' style='padding:48px;text-align:center;color:var(--text-dim)'>No results found</div>"

    return HTMLResponse(html)
