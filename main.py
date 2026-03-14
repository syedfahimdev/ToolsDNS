"""
main.py — FastAPI application entry point for ToolDNS.

Initializes all components (database, embedder, search engine,
ingestion pipeline) and starts the FastAPI server with the
API routes.

The server can be started in two ways:
    1. CLI:    python -m tooldns.cli serve
    2. Direct: python main.py
    3. Uvicorn: uvicorn main:app --port 8787

API documentation is auto-generated at /docs (Swagger UI).
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from tooldns.config import settings, logger
from tooldns.database import ToolDatabase
from tooldns.embedder import get_embedder
from tooldns.search import SearchEngine
from tooldns.ingestion import IngestionPipeline
from tooldns.api import router, init_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle manager.

    Initializes all components on startup:
    1. Database (SQLite)
    2. Embedding model (sentence-transformers)
    3. Search engine
    4. Ingestion pipeline

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

    yield  # App is running

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
)

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
        "sources": len(db.get_all_sources())
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False
    )
