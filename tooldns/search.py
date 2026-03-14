"""
search.py — Semantic search engine for ToolDNS.

search.py — Hybrid search engine for ToolDNS.

Performs hybrid search combining:
    1. Semantic similarity (cosine) between embedding vectors
    2. BM25 keyword matching via SQLite FTS5

The hybrid approach ensures:
    - Natural language queries work well (semantic)
    - Exact tool name lookups work too (BM25)
    - E.g. "GMAIL_SEND_EMAIL" matches by name, "send email" by meaning

Scoring formula:
    hybrid_score = (semantic_weight × cosine) + (bm25_weight × bm25_normalized)
    Default: semantic=0.7, bm25=0.3

Performance:
    - For <10,000 tools, brute-force cosine + FTS5 is fast enough (<100ms)
    - For larger indexes, upgrade to vector DB (Qdrant, pgvector, FAISS)

Usage:
    from tooldns.search import SearchEngine
    engine = SearchEngine(database, embedder)
    results = engine.search("create a github issue", top_k=3)
"""

import json
import time
import os
import numpy as np
from tooldns.config import logger, settings
from tooldns.database import ToolDatabase
from tooldns.embedder import Embedder
from tooldns.models import SearchResult, SearchResponse
from tooldns.tokens import count_tool_tokens, get_model_price, tokens_to_cost


class SearchEngine:
    """
    Semantic search over the tool index.

    Takes a natural language query, embeds it, and finds the most
    similar tools by cosine similarity. Returns ranked results with
    confidence scores and real token-savings analytics (not estimates).

    Token counts are computed from actual tool schemas using tiktoken.
    The total index token count is cached in memory and invalidated
    when the tool count changes, so it's computed at most once per
    ingestion cycle.

    Attributes:
        db: The ToolDatabase instance containing indexed tools.
        embedder: The Embedder instance for query embedding.
    """

    # Default weights for hybrid scoring
    SEMANTIC_WEIGHT = 0.7
    BM25_WEIGHT = 0.3

    def __init__(self, db: ToolDatabase, embedder: Embedder):
        """
        Initialize the search engine.

        Args:
            db: Database containing indexed tools with embeddings.
            embedder: Embedder for converting search queries to vectors.
        """
        self.db = db
        self.embedder = embedder
        # Cache for total index token count — recomputed when tool count changes
        self._cached_index_tokens: int = 0
        self._cached_tool_count: int = 0

    def _get_index_tokens(self, all_tools: list[dict]) -> int:
        """
        Get total token count for all tools in the index.

        Cached in memory keyed by tool count. Recomputed only when the
        number of indexed tools changes (i.e. after a re-ingest).

        Args:
            all_tools: All tools from the database (without embeddings).

        Returns:
            int: Total tokens for the full tool index.
        """
        tool_count = len(all_tools)
        if tool_count != self._cached_tool_count or self._cached_index_tokens == 0:
            self._cached_index_tokens = sum(count_tool_tokens(t) for t in all_tools)
            self._cached_tool_count = tool_count
        return self._cached_index_tokens

    def _get_model(self) -> str:
        """
        Detect which LLM model is being used.

        Checks TOOLDNS_MODEL env var first, then reads nanobot's
        config.json, then falls back to empty string (unknown).

        Returns:
            str: Model name, e.g. "claude-sonnet-4-6".
        """
        # Explicit override — env var or settings
        model = os.environ.get("TOOLDNS_MODEL", "").strip() or settings.model.strip()
        if model:
            return model

        # Try reading nanobot config
        try:
            nanobot_cfg = os.path.expanduser("~/.nanobot/config.json")
            with open(nanobot_cfg) as f:
                cfg = json.load(f)
            model = cfg.get("model", "")
            if not model:
                # Check agents.defaults
                agents = cfg.get("agents", {})
                model = (agents.get("defaults") or {}).get("model", "")
            if model:
                return model
        except Exception:
            pass

        return ""

    def search(self, query: str, top_k: int = 3,
               threshold: float = 0.1) -> SearchResponse:
        """
        Search for tools matching a natural language query.

        Embeds the query, computes cosine similarity against all indexed
        tool embeddings, and returns the top matches above the confidence
        threshold.

        Logs every search to the database with real token counts (not
        estimates) so the stats UI can show accurate savings.

        Args:
            query: Natural language description of the needed tool.
            top_k: Maximum number of results to return (default: 3).
            threshold: Minimum confidence score (0.0-1.0) to include.

        Returns:
            SearchResponse: Ranked results with real token savings data.
        """
        start_time = time.time()

        # Embed the query
        query_embedding = self.embedder.embed(query)

        # Get all tools with embeddings for scoring
        all_tools = self.db.get_all_tools_with_embeddings()
        total_tools = len(all_tools)

        if not all_tools:
            return SearchResponse(
                results=[],
                total_tools_indexed=0,
                tokens_saved=0,
                search_time_ms=0.0
            )

        # BM25 keyword scores
        bm25_scores = self.db.bm25_search(query, limit=50)

        # Score every tool
        scored_tools = []
        for tool in all_tools:
            embedding = tool.get("embedding", [])
            if not embedding:
                continue
            semantic_score = self._cosine_similarity(query_embedding, embedding)
            bm25_score = bm25_scores.get(tool["id"], 0.0)
            hybrid = self.SEMANTIC_WEIGHT * semantic_score + self.BM25_WEIGHT * bm25_score
            if hybrid >= threshold:
                scored_tools.append((tool, hybrid))

        scored_tools.sort(key=lambda x: x[1], reverse=True)
        top_results = scored_tools[:top_k]

        # Build result objects
        results = []
        for tool, confidence in top_results:
            source_info = tool.get("source_info", {})
            results.append(SearchResult(
                id=tool["id"],
                name=tool["name"],
                description=tool["description"],
                confidence=round(confidence, 4),
                input_schema=tool.get("input_schema", {}),
                source=source_info.get("source_name", "unknown"),
                how_to_call=self._build_call_instructions(source_info)
            ))

        search_time = (time.time() - start_time) * 1000

        # --- Real token counting (not estimates) ---
        # tokens_full_index: what the LLM would have consumed loading all tools
        tokens_full_index = self._get_index_tokens(all_tools)
        # tokens_returned: what ToolDNS actually sent back
        tokens_returned = sum(count_tool_tokens(t) for t, _ in top_results)
        tokens_saved = max(0, tokens_full_index - tokens_returned)

        # Model-aware cost calculation
        model_name = self._get_model()
        price = get_model_price(model_name) if model_name else None
        cost_saved = tokens_to_cost(tokens_saved, price) if price else 0.0

        # Log to DB (async-safe: one connection per call)
        try:
            self.db.log_search(
                query=query[:500],
                total_tools_in_index=total_tools,
                tools_returned=len(results),
                tokens_full_index=tokens_full_index,
                tokens_returned=tokens_returned,
                tokens_saved=tokens_saved,
                model_name=model_name,
                price_per_million=price or 0.0,
                cost_saved_usd=cost_saved,
                search_time_ms=round(search_time, 2),
            )
        except Exception as e:
            logger.warning(f"Failed to log search: {e}")

        logger.info(
            f"Search '{query[:50]}' → {len(results)}/{total_tools} tools, "
            f"{search_time:.1f}ms, {tokens_saved:,} tokens saved"
            + (f" (${cost_saved:.4f} @ {model_name})" if price else "")
        )

        return SearchResponse(
            results=results,
            total_tools_indexed=total_tools,
            tokens_saved=tokens_saved,
            search_time_ms=round(search_time, 2)
        )

    def _cosine_similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """
        Compute cosine similarity between two vectors using numpy.

        Since our embeddings are already L2-normalized (from sentence-transformers
        with normalize_embeddings=True), cosine similarity equals the dot product.

        Args:
            vec_a: First embedding vector.
            vec_b: Second embedding vector.

        Returns:
            float: Similarity score (0.0 = unrelated, 1.0 = identical).
        """
        if len(vec_a) != len(vec_b):
            return 0.0
        return float(np.dot(vec_a, vec_b))

    def _build_call_instructions(self, source_info: dict) -> dict:
        """
        Build instructions for how to call a discovered tool.

        Based on the tool's source type, provides the LLM with
        the information it needs to actually invoke the tool
        (e.g., which MCP server to call, or which API endpoint).

        Args:
            source_info: The tool's provenance metadata.

        Returns:
            dict: Instructions for calling the tool.
        """
        source_type = source_info.get("source_type", "")
        server = source_info.get("server", "")

        if "mcp" in source_type or "stdio" in source_type:
            return {
                "type": "mcp",
                "server": server,
                "tool_name": source_info.get("original_name", ""),
                "instruction": f"Call this tool via the '{server}' MCP server."
            }
        elif "skill" in source_type:
            return {
                "type": "skill",
                "skill_source": server,
                "instruction": "Use the skill template to construct the API call."
            }
        elif "custom" in source_type:
            return {
                "type": "custom",
                "instruction": "Call this tool using its input schema."
            }
        else:
            return {
                "type": "unknown",
                "source": server,
                "instruction": "Refer to the tool's source for calling instructions."
            }
