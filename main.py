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
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from tooldns.config import settings, logger
from tooldns.database import ToolDatabase
from tooldns.embedder import get_embedder
from tooldns.search import SearchEngine
from tooldns.ingestion import IngestionPipeline
from tooldns.api import router, init_api

# Rate limiter — keyed by IP (all requests go through localhost so key by API key header instead)
def _get_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    return auth or get_remote_address(request)

limiter = Limiter(key_func=_get_key, default_limits=["120/minute"])


async def _auto_refresh(pipeline: IngestionPipeline, interval_min: int):
    """
    Background task that periodically re-ingests all registered sources.

    Runs in a loop, sleeping for `interval_min` minutes between each
    refresh cycle. Errors are logged but never crash the refresh loop.

    Args:
        pipeline: The IngestionPipeline instance.
        interval_min: Minutes between each refresh cycle.
    """
    while True:
        await asyncio.sleep(interval_min * 60)
        try:
            logger.info("Auto-refresh: re-ingesting all sources...")
            total = pipeline.ingest_all()
            logger.info(f"Auto-refresh complete: {total} tools indexed")
        except Exception as e:
            logger.error(f"Auto-refresh error: {e}")


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

    # Preload the embedding model
    logger.info("Preloading embedding model...")
    embedder.preload()

    # Inject into API routes
    init_api(search_engine, pipeline, db)

    tool_count = db.get_tool_count()
    source_count = len(db.get_all_sources())
    logger.info(
        f"ToolDNS ready — {tool_count} tools from {source_count} sources"
    )

    # Start auto-refresh if interval is set
    refresh_task = None
    if settings.refresh_interval > 0:
        logger.info(
            f"Auto-refresh enabled: every {settings.refresh_interval} min"
        )
        refresh_task = asyncio.create_task(
            _auto_refresh(pipeline, settings.refresh_interval)
        )

    yield  # App is running

    # Cleanup
    if refresh_task:
        refresh_task.cancel()
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

# Attach rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(router)


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
