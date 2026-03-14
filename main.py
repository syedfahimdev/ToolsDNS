"""
main.py — FastAPI application entry point for ToolDNS.

Initializes all components (database, embedder, search engine,
ingestion pipeline) and starts the FastAPI server with the
API routes. Includes an optional background auto-refresh scheduler.

The server can be started in three ways:
    1. CLI:    python3 -m tooldns.cli serve
    2. Direct: python3 main.py
    3. Uvicorn: uvicorn main:app --port 8787

API documentation is auto-generated at /docs (Swagger UI).
"""

import asyncio
import ipaddress
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from tooldns.config import settings, logger
from tooldns.database import ToolDatabase
from tooldns.embedder import get_embedder
from tooldns.search import SearchEngine
from tooldns.ingestion import IngestionPipeline
from tooldns.health import HealthMonitor
from tooldns.api import router, init_api
from tooldns.ui import ui_router, init_ui
from tooldns.auth import init_auth

# ---------------------------------------------------------------------------
# Network access control middleware
# ---------------------------------------------------------------------------

# Tailscale always uses 100.64.0.0/10
_TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_LOCALHOST = {"127.0.0.1", "::1"}


def _is_tailscale(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in _TAILSCALE_NETWORK
    except ValueError:
        return False


class NetworkACLMiddleware(BaseHTTPMiddleware):
    """
    Enforce network-based access control:
      - /ui/*  paths: Tailscale (100.64.0.0/10) OR localhost
      - /v1/*  paths: localhost only
      - /health, /: localhost OR Tailscale (monitoring)
      - Everything else: block from non-localhost non-Tailscale IPs
    """

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        is_local = client_ip in _LOCALHOST
        is_ts = _is_tailscale(client_ip)

        # /v1/* (API) — localhost only
        if path.startswith("/v1/"):
            if not is_local:
                return JSONResponse(
                    {"detail": "API access restricted to localhost"},
                    status_code=403
                )

        # /ui/* — Tailscale or localhost
        elif path.startswith("/ui") or path.startswith("/static"):
            if not (is_local or is_ts):
                return HTMLResponse(
                    "<h1>403 Forbidden</h1><p>UI is accessible via Tailscale only.</p>",
                    status_code=403
                )

        # Everything else (/, /health, /docs) — allow local + Tailscale, block public
        else:
            if not (is_local or is_ts):
                return JSONResponse({"detail": "Access denied"}, status_code=403)

        return await call_next(request)


# Rate limiter — keyed by API key header
def _get_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    return auth or get_remote_address(request)

limiter = Limiter(key_func=_get_key, default_limits=["120/minute"])


async def _auto_refresh(pipeline: IngestionPipeline, interval_min: int):
    """Background task: re-ingest all sources every interval_min minutes.

    Runs once immediately at startup (after a short delay) to register local
    skill sources, then repeats on the configured interval.
    """
    await asyncio.sleep(5)  # Short delay to let server finish starting
    while True:
        try:
            logger.info("Auto-refresh: re-ingesting all sources...")
            loop = asyncio.get_event_loop()
            total = await loop.run_in_executor(None, pipeline.ingest_all)
            logger.info(f"Auto-refresh complete: {total} tools indexed")
        except Exception as e:
            logger.error(f"Auto-refresh error: {e}")
        await asyncio.sleep(interval_min * 60)


async def _config_watcher(pipeline: IngestionPipeline, config_path: Path):
    """
    Watch ~/.tooldns/config.json for changes and trigger re-ingest.

    Uses watchdog for OS-level inotify/kqueue events. A lock prevents
    concurrent ingests if the file is saved multiple times quickly.
    """
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    loop = asyncio.get_running_loop()
    _last_mtime = [config_path.stat().st_mtime if config_path.exists() else 0]
    _lock = asyncio.Lock()

    async def _reload():
        if _lock.locked():
            return  # ingest already queued/running, skip duplicate
        async with _lock:
            await asyncio.sleep(0.5)  # settle so the file is fully written
            try:
                total = await loop.run_in_executor(None, pipeline.ingest_all)
                logger.info(f"Hot-reload complete: {total} tools indexed")
            except Exception as e:
                logger.error(f"Hot-reload error: {e}")

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if Path(event.src_path).resolve() != config_path.resolve():
                return
            try:
                mtime = config_path.stat().st_mtime
            except OSError:
                return
            if mtime == _last_mtime[0]:
                return  # spurious duplicate event
            _last_mtime[0] = mtime
            logger.info("config.json changed — triggering re-ingest...")
            asyncio.run_coroutine_threadsafe(_reload(), loop)

    observer = Observer()
    observer.schedule(_Handler(), str(config_path.parent), recursive=False)
    observer.start()
    logger.info(f"Hot-reload watching: {config_path}")
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        observer.stop()
        observer.join()


async def _health_check_loop(monitor: HealthMonitor, interval_sec: int = 60):
    """Background task: check source health every interval_sec seconds."""
    await asyncio.sleep(10)  # Wait for server to fully start
    while True:
        try:
            await monitor.check_all()
        except Exception as e:
            logger.error(f"Health check error: {e}")
        await asyncio.sleep(interval_sec)


def _ensure_mcporter_system_config():
    """
    Write ~/.mcporter/mcporter.json on first run so agents can call
    ToolDNS via `mcporter call tooldns ...` without --config flag.

    Only writes if tooldns is not already registered there.
    Safe to call on every startup — idempotent.
    """
    import json
    mcporter_dir = Path(os.path.expanduser("~/.mcporter"))
    mcporter_cfg = mcporter_dir / "mcporter.json"

    # Read existing config or start fresh
    cfg = {}
    if mcporter_cfg.exists():
        try:
            cfg = json.loads(mcporter_cfg.read_text())
        except Exception:
            cfg = {}

    servers = cfg.setdefault("mcpServers", {})
    if "tooldns" not in servers:
        servers["tooldns"] = {
            "command": "python3",
            "args": ["-m", "tooldns.mcp_server"],
            "lifecycle": {"mode": "keep-alive"},
        }
        mcporter_dir.mkdir(parents=True, exist_ok=True)
        mcporter_cfg.write_text(json.dumps(cfg, indent=4))
        logger.info(f"Registered tooldns in {mcporter_cfg}")


def _clean_stale_sources(db, pipeline):
    """
    Remove sources whose DB ID doesn't match the hash of their name+type.

    These are orphans created by old code paths. Without this cleanup they
    accumulate as duplicates every time ingest_all() runs.
    """
    import hashlib
    sources = db.get_all_sources()
    for source in sources:
        config = dict(source.get("config") or {})
        config["name"] = source["name"]
        config["type"] = source["type"]
        expected = hashlib.md5(
            f"{config.get('name','')}:{config.get('type','')}".encode()
        ).hexdigest()[:12]
        if source["id"] != expected:
            logger.info(
                f"Removing stale source {source['id']} ('{source['name']}') "
                f"— expected ID {expected}"
            )
            db.delete_source(source["id"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle manager.

    Initializes all components on startup:
    1. Database (SQLite)
    2. Embedding model (sentence-transformers)
    3. Search engine
    4. Ingestion pipeline
    5. Auto-refresh background task (if interval > 0)

    The embedding model is preloaded to avoid first-request latency.
    """
    logger.info("Starting ToolDNS...")

    # Initialize components
    db = ToolDatabase(settings.db_path)
    embedder = get_embedder()
    search_engine = SearchEngine(db, embedder)
    pipeline = IngestionPipeline(db, embedder)
    health_monitor = HealthMonitor(db)

    # Mark any stale jobs from a previous crash as failed
    db.reset_stale_jobs()

    # First-run setup: write system-level mcporter config so agents can call
    # ToolDNS via `mcporter call tooldns ...` without needing --config flag.
    _ensure_mcporter_system_config()
    _clean_stale_sources(db, pipeline)

    # Preload the embedding model
    logger.info("Preloading embedding model...")
    embedder.preload()

    cache_stats = db.get_embedding_cache_stats()
    logger.info(f"Embedding cache: {cache_stats['cached_embeddings']} vectors cached")

    # Inject into API and UI routes
    init_api(search_engine, pipeline, db, health_monitor)
    init_auth(db)
    init_ui(db, pipeline, health_monitor)

    tool_count = db.get_tool_count()
    source_count = len(db.get_all_sources())
    logger.info(
        f"ToolDNS ready — {tool_count} tools from {source_count} sources"
    )

    # Start background tasks
    tasks = []
    if settings.refresh_interval > 0:
        logger.info(f"Auto-refresh enabled: every {settings.refresh_interval} min")
        tasks.append(asyncio.create_task(_auto_refresh(pipeline, settings.refresh_interval)))

    tasks.append(asyncio.create_task(_health_check_loop(health_monitor, 60)))

    # Hot-reload: watch config.json for changes
    config_file = Path(settings.home) / "config.json"
    tasks.append(asyncio.create_task(_config_watcher(pipeline, config_file)))

    yield  # App is running

    # Cleanup
    for task in tasks:
        task.cancel()
    logger.info("ToolDNS shutting down.")


app = FastAPI(
    title="ToolDNS",
    description=(
        "DNS for AI Tools. Search 10,000 tools, return only the one you need. "
        "Point ToolDNS at your MCP servers, skill files, or APIs. "
        "When your LLM needs a tool, query ToolDNS — it finds the right one "
        "and returns only that schema, saving thousands of tokens."
    ),
    version="1.0.0",
    lifespan=lifespan,
    # Hide /docs and /openapi.json in production (security: don't expose schema publicly)
    docs_url="/docs" if settings.host == "127.0.0.1" else None,
    redoc_url=None,
)

# Attach access control middleware (network ACL: Tailscale for UI, localhost for API)
app.add_middleware(NetworkACLMiddleware)

# Attach rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

from fastapi.staticfiles import StaticFiles
import os as _os

app.include_router(router)
app.include_router(ui_router)

# Mount static files (CSS, JS) for the web UI
_static_dir = _os.path.join(_os.path.dirname(__file__), "tooldns", "static")
if _os.path.exists(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/")
async def root():
    """Health check and welcome endpoint."""
    return {
        "service": "ToolDNS",
        "version": "1.0.0",
        "description": "DNS for AI Tools",
        "docs": "/docs"
    }


@app.get("/health")
async def health():
    """Health check endpoint for monitoring."""
    db = ToolDatabase(settings.db_path)
    return {
        "status": "healthy",
        "tools_indexed": db.get_tool_count(),
        "sources": len(db.get_all_sources()),
        "refresh_interval_min": settings.refresh_interval,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False
    )
