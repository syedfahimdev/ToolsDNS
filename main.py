"""
main.py — FastAPI application entry point for ToolsDNS.

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
import base64
import ipaddress
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, Response
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
from tooldns.api import router, admin_router, init_api
from tooldns.auth import init_auth
from tooldns.mcp_server import mcp as _mcp_server, _request_api_key as _mcp_request_key

# Build the MCP ASGI app once at module level so its lifespan can be composed
# with the FastAPI lifespan below (required by fastmcp's task group init).
_mcp_http_app = _mcp_server.http_app(
    path="/",
    transport="streamable-http",
    # stateless_http=True: every request is independent — no session ID is
    # issued or required.  Clients like copaw/agentscope that cache session IDs
    # from a previous connection would otherwise get 404 "Invalid or expired
    # session ID" after a server restart.  Stateless mode eliminates all
    # session-state tracking while keeping full tool functionality.
    stateless_http=True,
)

# ---------------------------------------------------------------------------
# Network access control middleware
# ---------------------------------------------------------------------------

# Tailscale always uses 100.64.0.0/10
_TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")
# Docker bridge / private networks (Caddy proxy arrives as these)
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("172.16.0.0/12"),   # Docker default
    ipaddress.ip_network("10.0.0.0/8"),       # Private
    ipaddress.ip_network("192.168.0.0/16"),   # Private
]
_LOCALHOST = {"127.0.0.1", "::1"}


def _is_tailscale(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in _TAILSCALE_NETWORK
    except ValueError:
        return False


def _is_private(ip: str) -> bool:
    """Returns True for Docker bridge / private network IPs (reverse proxy traffic)."""
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


class NetworkACLMiddleware(BaseHTTPMiddleware):
    """
    Enforce network-based access control:
      - /v1/* and /mcp: localhost, Tailscale, or private networks (Caddy proxy)
      - /dl/{token} (GET): public — anyone can download a file by token
      - /dl/upload (POST): private networks only (Caddy / internal callers)
      - Everything else (/health, /docs, /): localhost + Tailscale + private
    """

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        is_local = client_ip in _LOCALHOST
        is_ts = _is_tailscale(client_ip)
        is_private = _is_private(client_ip)  # Docker/Caddy proxy

        # /dl/{token} GET — public download links, no IP restriction
        if path.startswith("/dl/") and request.method == "GET":
            return await call_next(request)

        # /v1/* (API) and /mcp — protected by API key, allow proxy + local + TS
        if path.startswith("/v1/") or path.startswith("/mcp"):
            if not (is_local or is_ts or is_private):
                return JSONResponse(
                    {"detail": "API access requires routing through the reverse proxy"},
                    status_code=403
                )

        # Everything else (/dl/upload POST, /, /health, /docs) — private only
        else:
            if not (is_local or is_ts or is_private):
                return JSONResponse({"detail": "Access denied"}, status_code=403)

        return await call_next(request)


class MCPKeyMiddleware(BaseHTTPMiddleware):
    """
    For /mcp requests:
      1. Inject required Accept headers so MCP clients that don't send
         'Accept: application/json, text/event-stream' (e.g. copaw/agentscope)
         are not rejected with 406 Not Acceptable by fastmcp's transport layer.
      2. Extract the caller's Bearer token and store it in _request_api_key
         ContextVar so MCP tools forward it to internal API calls.

    This ensures search_tools() credits usage + tokens to the caller's sub-key
    rather than always using the admin key.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            # --- Path normalisation ---
            # Starlette's Mount redirects /mcp → /mcp/ with a 307.
            # Many MCP clients (copaw) don't follow 307 on POST requests.
            # Rewrite the scope path in-place so Mount matches directly.
            if request.scope.get("path") == "/mcp":
                request.scope["path"] = "/mcp/"
                request.scope["raw_path"] = b"/mcp/"

            # --- Accept header injection ---
            # fastmcp's streamable-http transport enforces:
            #   Accept: application/json, text/event-stream
            # Many MCP clients (copaw, older agentscope) don't send this header.
            # We inject it into the ASGI scope before the request reaches fastmcp
            # so these clients work without modification.
            accept = request.headers.get("accept", "")
            needs_json = "application/json" not in accept
            needs_sse = "text/event-stream" not in accept
            if needs_json or needs_sse:
                parts = [a.strip() for a in accept.split(",") if a.strip()]
                if needs_json:
                    parts.append("application/json")
                if needs_sse:
                    parts.append("text/event-stream")
                new_accept = ", ".join(parts).encode()
                # Replace the accept entry in the raw ASGI scope headers
                new_headers = [
                    (k, v) for k, v in request.scope["headers"]
                    if k.lower() != b"accept"
                ]
                new_headers.append((b"accept", new_accept))
                request.scope["headers"] = new_headers

            # --- Bearer token extraction ---
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:].strip()
                if token:
                    _mcp_request_key.set(token)
        return await call_next(request)


# Rate limiter — keyed by API key header
def _get_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    return auth or get_remote_address(request)

limiter = Limiter(key_func=_get_key, default_limits=["120/minute"])


async def _auto_refresh(pipeline: IngestionPipeline, interval_min: int, search_engine=None):
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
            if search_engine:
                search_engine.invalidate_cache()
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
    ToolsDNS via `mcporter call tooldns ...` without --config flag.

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
    logger.info("Starting ToolsDNS...")

    # Initialize components
    db = ToolDatabase(settings.db_path)
    embedder = get_embedder()
    search_engine = SearchEngine(db, embedder)
    pipeline = IngestionPipeline(db, embedder)
    health_monitor = HealthMonitor(db)

    # Mark any stale jobs from a previous crash as failed
    db.reset_stale_jobs()

    # First-run setup: write system-level mcporter config so agents can call
    # ToolsDNS via `mcporter call tooldns ...` without needing --config flag.
    _ensure_mcporter_system_config()
    _clean_stale_sources(db, pipeline)

    # Preload the embedding model
    logger.info("Preloading embedding model...")
    embedder.preload()

    cache_stats = db.get_embedding_cache_stats()
    logger.info(f"Embedding cache: {cache_stats['cached_embeddings']} vectors cached")

    # Inject dependencies into API routes
    init_api(search_engine, pipeline, db, health_monitor)
    init_auth(db)

    tool_count = db.get_tool_count()
    source_count = len(db.get_all_sources())
    logger.info(
        f"ToolsDNS ready — {tool_count} tools from {source_count} sources"
    )

    # Pre-warm the in-memory embedding matrix so first search is instant
    # (without this, first search builds the matrix at request time — ~500ms extra)
    if tool_count > 0:
        logger.info("Pre-warming embedding matrix...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, search_engine._get_embedding_matrix)
        logger.info("Embedding matrix ready — first search will be fast")

    # Start background tasks
    tasks = []
    if settings.refresh_interval > 0:
        logger.info(f"Auto-refresh enabled: every {settings.refresh_interval} min")
        tasks.append(asyncio.create_task(_auto_refresh(pipeline, settings.refresh_interval, search_engine)))

    tasks.append(asyncio.create_task(_health_check_loop(health_monitor, 60)))

    # Hot-reload: watch config.json for changes
    config_file = Path(settings.home) / "config.json"
    tasks.append(asyncio.create_task(_config_watcher(pipeline, config_file)))

    # Start the MCP ASGI app's lifespan (initializes the task group required
    # by fastmcp's StreamableHTTPSessionManager — must run while app is live)
    async with _mcp_http_app.lifespan(app):
        yield  # App is running

    # Cleanup
    for task in tasks:
        task.cancel()
    logger.info("ToolsDNS shutting down.")


app = FastAPI(
    title="ToolsDNS",
    description=(
        "DNS for AI Tools. Search 10,000 tools, return only the one you need. "
        "Point ToolsDNS at your MCP servers, skill files, or APIs. "
        "When your LLM needs a tool, query ToolsDNS — it finds the right one "
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

# Capture the caller's Bearer token on /mcp requests so MCP tools credit
# usage + tokens to the correct sub-key (not the admin key).
app.add_middleware(MCPKeyMiddleware)

# Attach rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(router)
app.include_router(admin_router)

# Mount the MCP server at /mcp (streamable-HTTP transport)
# Users connect with: https://your-domain.com/mcp  +  Authorization: Bearer <api-key>
app.mount("/mcp", _mcp_http_app)


# ---------------------------------------------------------------------------
# Public file download store — no API key required, UUID token-based, 15-min TTL
# Used by skill tools to hand off generated files without putting base64 in LLM context
# ---------------------------------------------------------------------------

_DOWNLOAD_DIR = Path("/tmp/tooldns-downloads")
_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
_DOWNLOAD_TTL = 900  # 15 minutes


def register_download(filename: str, data: bytes) -> str:
    """Save bytes to the download store and return an opaque token."""
    token = uuid.uuid4().hex
    dest = _DOWNLOAD_DIR / token
    dest.write_bytes(data)
    # Store original filename alongside
    (dest.with_suffix(".name")).write_text(filename)
    return token


def _purge_expired_downloads():
    """Remove download files older than TTL."""
    cutoff = time.time() - _DOWNLOAD_TTL
    for f in _DOWNLOAD_DIR.iterdir():
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


@app.post("/dl/upload")
async def upload_file(request: Request):
    """
    Upload a file to the download store and get a download URL back.
    No API key required (protected by network ACL — private/Tailscale only).
    Accepts multipart/form-data with a 'file' field OR raw bytes with
    X-Filename header. Returns {"download_url": "...", "token": "..."}.
    """
    _purge_expired_downloads()
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        from fastapi import UploadFile
        form = await request.form()
        upload = form.get("file")
        filename = getattr(upload, "filename", None) or "file"
        data = await upload.read()
    else:
        filename = request.headers.get("X-Filename", "file")
        data = await request.body()
    if not data:
        return JSONResponse({"error": "No file data received"}, status_code=400)
    token = register_download(filename, data)
    base_url = os.environ.get("TOOLDNS_PUBLIC_URL", f"http://127.0.0.1:{settings.port}").rstrip("/")
    download_url = f"{base_url}/dl/{token}"
    return {"download_url": download_url, "token": token, "filename": filename}


@app.get("/dl/{token}")
async def download_file(token: str):
    """
    Public file download endpoint — no API key required.
    Token is a UUID hex issued by register_download().
    Files expire after 15 minutes.
    """
    _purge_expired_downloads()
    dest = _DOWNLOAD_DIR / token
    if not dest.exists():
        return JSONResponse({"error": "File not found or expired"}, status_code=404)
    name_file = dest.with_suffix(".name")
    filename = name_file.read_text() if name_file.exists() else "file.xlsx"
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if filename.endswith(".pdf"):
        mime = "application/pdf"
    elif filename.endswith(".csv"):
        mime = "text/csv"
    return FileResponse(dest, media_type=mime, filename=filename)


@app.get("/")
async def root():
    """API entry point — returns service info."""
    return {
        "name": "ToolsDNS",
        "docs": "/docs",
        "health": "/health",
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
