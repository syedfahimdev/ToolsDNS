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
            "type": SourceType.SKILL_DIRECTORY,
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
