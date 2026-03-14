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
from tooldns.config import logger
from tooldns.database import ToolDatabase
from tooldns.embedder import Embedder
from tooldns.models import SearchResult, SearchResponse


class SearchEngine:
    """
    Semantic search over the tool index.

    Takes a natural language query, embeds it, and finds the most
    similar tools by cosine similarity. Returns ranked results with
    confidence scores and token-savings analytics.

    Attributes:
        db: The ToolDatabase instance containing indexed tools.
        embedder: The Embedder instance for query embedding.
    """

    # Average tokens per tool schema — used for tokens_saved calculation.
    AVG_TOKENS_PER_TOOL = 120

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

    def search(self, query: str, top_k: int = 3,
               threshold: float = 0.1) -> SearchResponse:
        """
        Search for tools matching a natural language query.

        Embeds the query, computes cosine similarity against all indexed
        tool embeddings, and returns the top matches above the confidence
        threshold.

        Args:
            query: Natural language description of the needed tool.
                   Example: "create a github issue about the login bug"
            top_k: Maximum number of results to return (default: 3).
            threshold: Minimum confidence score (0.0-1.0) to include
                      a result (default: 0.5).

        Returns:
            SearchResponse: Contains ranked results, total tool count,
                           tokens saved estimate, and search time.
        """
        start_time = time.time()

        # Embed the query
        query_embedding = self.embedder.embed(query)

        # Get all tools with embeddings
        all_tools = self.db.get_all_tools_with_embeddings()
        total_tools = len(all_tools)

        if not all_tools:
            return SearchResponse(
                results=[],
                total_tools_indexed=0,
                tokens_saved=0,
                search_time_ms=0.0
            )

        # Compute cosine similarity for each tool
        scored_tools = []

        # Get BM25 keyword scores
        bm25_scores = self.db.bm25_search(query, limit=50)

        for tool in all_tools:
            embedding = tool.get("embedding", [])
            if not embedding:
                continue

            # Semantic score (cosine similarity)
            semantic_score = self._cosine_similarity(query_embedding, embedding)

            # BM25 score (keyword match), defaults to 0 if no match
            bm25_score = bm25_scores.get(tool["id"], 0.0)

            # Hybrid score
            hybrid = (
                self.SEMANTIC_WEIGHT * semantic_score +
                self.BM25_WEIGHT * bm25_score
            )

            if hybrid >= threshold:
                scored_tools.append((tool, hybrid))

        # Sort by similarity (highest first) and take top_k
        scored_tools.sort(key=lambda x: x[1], reverse=True)
        top_results = scored_tools[:top_k]

        # Build response
        results = []
        for tool, confidence in top_results:
            source_info = tool.get("source_info", {})

            # Build "how to call" instructions based on source type
            how_to_call = self._build_call_instructions(source_info)

            results.append(SearchResult(
                id=tool["id"],
                name=tool["name"],
                description=tool["description"],
                confidence=round(confidence, 4),
                input_schema=tool.get("input_schema", {}),
                source=source_info.get("source_name", "unknown"),
                how_to_call=how_to_call
            ))

        # Calculate tokens saved: all tools' schemas minus returned schemas
        returned_count = len(results)
        tokens_saved = max(0,
            (total_tools - returned_count) * self.AVG_TOKENS_PER_TOOL
        )

        search_time = (time.time() - start_time) * 1000

        logger.info(
            f"Search '{query[:50]}' → {len(results)} results "
            f"(from {total_tools} tools, {search_time:.1f}ms, "
            f"~{tokens_saved} tokens saved)"
        )

        return SearchResponse(
            results=results,
            total_tools_indexed=total_tools,
            tokens_saved=tokens_saved,
            search_time_ms=round(search_time, 2)
        )

    def _cosine_similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """
        Compute cosine similarity between two vectors.

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
        return sum(a * b for a, b in zip(vec_a, vec_b))

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
