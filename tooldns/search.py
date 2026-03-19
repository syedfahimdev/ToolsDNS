"""
search.py — Hybrid search engine for ToolsDNS.

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
import threading
from collections import OrderedDict
from typing import Optional
import numpy as np
from tooldns.config import logger, settings
from tooldns.cache import create_cache
from tooldns.database import ToolDatabase
from tooldns.embedder import Embedder
from tooldns.models import SearchResult, SearchResponse
from tooldns.tokens import count_tool_tokens, get_model_price, tokens_to_cost


class _SearchCache:
    """
    Thread-safe LRU cache for search results.

    Keyed on (query, top_k, threshold). Entries expire after `ttl_secs`
    seconds. Max `maxsize` entries — oldest evicted when full.
    Invalidated entirely on ingestion so stale tool data is never served.
    """

    def __init__(self, maxsize: int = 256, ttl_secs: float = 60.0):
        self._cache: OrderedDict[tuple, tuple] = OrderedDict()  # key → (expires_at, SearchResponse)
        self._maxsize = maxsize
        self._ttl = ttl_secs
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: tuple) -> "SearchResponse | None":
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, response = entry
            if time.monotonic() > expires_at:
                del self._cache[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._hits += 1
            return response

    def set(self, key: tuple, response: "SearchResponse") -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (time.monotonic() + self._ttl, response)
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
                "size": len(self._cache),
                "maxsize": self._maxsize,
                "ttl_secs": self._ttl,
            }


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
        # In-memory embedding matrix — rebuilt after ingestion, used on every search
        self._emb_matrix: np.ndarray | None = None   # shape (N, D)
        self._emb_tools: list[dict] = []              # parallel list of tool dicts (no embedding field)
        self._emb_ids: list[str] = []                 # tool IDs in matrix row order
        self._emb_lock = threading.Lock()
        # Query result cache — avoids re-embedding identical queries within TTL
        self._cache = create_cache(maxsize=256, ttl_secs=60.0)

    def invalidate_cache(self) -> None:
        """Drop the in-memory embedding matrix and query cache so next search reloads from DB."""
        with self._emb_lock:
            self._emb_matrix = None
            self._emb_tools = []
            self._emb_ids = []
        self._cache.clear()

    def warm_cache(self, top_n: int = 20) -> int:
        """Pre-run popular queries to warm the cache. Returns number warmed."""
        stats = self.db.get_search_stats()
        recent = stats.get("recent_searches", [])
        warmed = 0
        seen_queries = set()
        for entry in recent[:top_n]:
            q = entry.get("query", "").strip()
            if q and q not in seen_queries:
                seen_queries.add(q)
                try:
                    self.search(query=q, top_k=3, threshold=0.1)
                    warmed += 1
                except Exception:
                    pass
        return warmed

    def _get_embedding_matrix(self) -> tuple[np.ndarray, list[dict], list[str]]:
        """
        Return cached (matrix, tools, ids). Rebuild from DB if not yet loaded.

        The matrix stays in RAM across searches and is only rebuilt when
        invalidate_cache() is called (after every ingestion run).
        """
        with self._emb_lock:
            if self._emb_matrix is not None:
                return self._emb_matrix, self._emb_tools, self._emb_ids

            all_tools = self.db.get_all_tools_with_embeddings()
            vectors, tools, ids = [], [], []
            for t in all_tools:
                emb = t.get("embedding")
                if emb:
                    vectors.append(emb)
                    tool_copy = {k: v for k, v in t.items() if k != "embedding"}
                    tools.append(tool_copy)
                    ids.append(t["id"])

            self._emb_matrix = np.array(vectors, dtype=np.float32) if vectors else np.empty((0, 1), dtype=np.float32)
            self._emb_tools = tools
            self._emb_ids = ids
            logger.info(f"Embedding matrix built: {len(tools)} tools × {self._emb_matrix.shape[-1] if vectors else 0} dims")
            return self._emb_matrix, self._emb_tools, self._emb_ids

    def _log_search_safe(self, **kwargs) -> None:
        """Fire-and-forget wrapper for db.log_search; swallows exceptions."""
        try:
            self.db.log_search(**kwargs)
        except Exception as e:
            logger.warning(f"Failed to log search: {e}")

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

    # Model aliases that don't map to real pricing — treat as unknown
    _MODEL_ALIASES = {"auto-fastest", "auto", "default", "latest", "fastest"}

    def _get_model(self) -> str:
        """
        Detect which LLM model is being used.

        Checks TOOLDNS_MODEL env var first, then falls back to empty string.
        Skips aliases like 'auto-fastest' that don't map to real model IDs or pricing.

        Returns:
            str: Model name, e.g. "claude-sonnet-4-6", or "" if unknown.
        """
        def _valid(m: str) -> bool:
            return bool(m) and m.lower() not in self._MODEL_ALIASES

        # 1. Explicit override — env var or settings
        model = os.environ.get("TOOLDNS_MODEL", "").strip() or settings.model.strip()
        if _valid(model):
            return model

        return ""

    @staticmethod
    def trim_schema(schema: dict) -> dict:
        """
        Return a minimal version of a JSON Schema — required fields only.

        Strips optional parameters, lengthy descriptions, and nested examples
        to cut schema token count by ~70% while preserving enough info for
        an LLM to call the tool correctly.

        Args:
            schema: Full JSON Schema dict from the tool.

        Returns:
            dict: Trimmed schema with only required fields and their types.
        """
        if not schema:
            return {}
        props = schema.get("properties", {})
        required = set(schema.get("required", []))

        if required:
            # Keep only required fields, strip descriptions/examples/defaults
            trimmed_props = {}
            for k in required:
                if k not in props:
                    continue
                field = props[k]
                slim = {"type": field.get("type", "string")}
                # Keep enum values — agent needs them for correctness
                if "enum" in field:
                    slim["enum"] = field["enum"]
                # Keep items for arrays (need to know element type)
                if field.get("type") == "array" and "items" in field:
                    slim["items"] = {"type": field["items"].get("type", "string")}
                trimmed_props[k] = slim
        else:
            # No required fields — return top 5 most likely useful fields
            # Prefer shorter field names (usually simpler/core params)
            sorted_keys = sorted(props.keys(), key=len)[:5]
            trimmed_props = {}
            for k in sorted_keys:
                field = props[k]
                slim = {"type": field.get("type", "string")}
                if "enum" in field:
                    slim["enum"] = field["enum"]
                trimmed_props[k] = slim

        return {
            "type": "object",
            "properties": trimmed_props,
            "required": list(required),
            "_minimal": True,   # Flag so agents know this is a trimmed schema
        }

    def search(self, query: str, top_k: int = 3,
               threshold: float = 0.1, api_key: str = "",
               minimal: bool = False,
               allowed_tool_ids: Optional[set] = None,
               seen_tool_ids: Optional[set] = None,
               preference_boosts: Optional[dict[str, float]] = None) -> SearchResponse:
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

        # Cache key includes minimal + profile fingerprint so different modes don't collide
        allowed_key = frozenset(allowed_tool_ids) if allowed_tool_ids else None
        cache_key = (query.strip().lower(), top_k, round(threshold, 4), minimal, allowed_key)
        # Skip cache if session dedup is active (seen_tool_ids changes per-agent)
        if not seen_tool_ids:
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.info(f"Cache hit: '{query[:50]}' (stats: {self._cache.stats})")
                return cached

        # Detect real-time/web intent before searching
        is_realtime = self._is_realtime_query(query)
        expanded_query = self._expand_query(query) if is_realtime else query
        web_boost = 0.30 if is_realtime else 0.0

        emb_matrix, all_tools, tool_ids = self._get_embedding_matrix()
        total_tools = len(all_tools)

        if total_tools == 0:
            return SearchResponse(
                results=[],
                total_tools_indexed=0,
                tokens_saved=0,
                search_time_ms=0.0
            )

        # Primary search (with web boost when real-time intent detected)
        top_results, results = self._run_search(
            expanded_query, emb_matrix, all_tools, tool_ids, top_k, threshold,
            web_boost=web_boost, allowed_tool_ids=allowed_tool_ids,
            preference_boosts=preference_boosts,
        )

        # Fallback: if nothing found or best result is weak, try reformulated queries
        LOW_CONFIDENCE = 0.40
        if not results or results[0].confidence < LOW_CONFIDENCE:
            for fallback_q in self._generate_fallbacks(query, is_realtime=is_realtime):
                fb_top, fb_results = self._run_search(
                    fallback_q, emb_matrix, all_tools, tool_ids, top_k, threshold * 0.7,
                    web_boost=web_boost, allowed_tool_ids=allowed_tool_ids,
                    preference_boosts=preference_boosts,
                )
                if fb_results and fb_results[0].confidence >= 0.15:
                    top_results, results = fb_top, fb_results
                    expanded_query = fallback_q
                    logger.info(f"Fallback search succeeded with: '{fallback_q[:60]}'")
                    break

        search_time = (time.time() - start_time) * 1000

        # --- Real token counting (not estimates) ---
        tokens_full_index = self._get_index_tokens(all_tools)
        tokens_returned = sum(count_tool_tokens(t) for t, _ in top_results)
        tokens_saved = max(0, tokens_full_index - tokens_returned)

        model_name = self._get_model()
        price = get_model_price(model_name) if model_name else None
        cost_saved = tokens_to_cost(tokens_saved, price) if price else 0.0

        log_kwargs = dict(
            query=expanded_query[:500],
            total_tools_in_index=total_tools,
            tools_returned=len(results),
            tokens_full_index=tokens_full_index,
            tokens_returned=tokens_returned,
            tokens_saved=tokens_saved,
            model_name=model_name,
            price_per_million=price or 0.0,
            cost_saved_usd=cost_saved,
            search_time_ms=round(search_time, 2),
            api_key=api_key,
        )
        threading.Thread(target=self._log_search_safe, kwargs=log_kwargs, daemon=True).start()

        logger.info(
            f"Search '{expanded_query[:50]}' → {len(results)}/{total_tools} tools, "
            f"{search_time:.1f}ms, {tokens_saved:,} tokens saved"
            + (f" (${cost_saved:.4f} @ {model_name})" if price else "")
        )

        # --- Session schema dedup ---
        # If caller passed seen_tool_ids, mark already-known tools and strip their schemas.
        # This saves tokens for repeat queries within an agent session.
        tokens_saved_by_dedup = 0
        if seen_tool_ids:
            for r in results:
                if r.id in seen_tool_ids:
                    # Calculate tokens we're about to save by not resending schema
                    schema_tokens = count_tool_tokens({"input_schema": r.input_schema})
                    tokens_saved_by_dedup += schema_tokens
                    r.already_seen = True
                    r.input_schema = {}  # Strip — agent already has it from earlier in session
                    r.description = f"[already seen — use cached schema] {r.description[:60]}"

        # --- Minimal schema mode ---
        # Strip optional fields from schemas — cuts per-result token cost ~70%.
        # Applied AFTER dedup so already_seen tools stay empty.
        if minimal:
            for r in results:
                if not r.already_seen and r.input_schema:
                    r.input_schema = self.trim_schema(r.input_schema)
                    # Also trim description to 100 chars — agent just needs the gist
                    if len(r.description) > 120:
                        r.description = r.description[:120] + "…"

        # Build a hint for the calling LLM when confidence is low
        hint = None
        top_conf = results[0].confidence if results else 0.0
        if not results:
            hint = (
                f"No tools found for '{query}'. "
                "STOP and rephrase your query. You might be asking for something "
                "ToolsDNS does not have. If you need general knowledge or real-time info (like 'price of tomato'), "
                "search for a 'web search' or 'browser' tool instead."
            )
        elif top_conf < LOW_CONFIDENCE:
            top_names = ", ".join(r.name for r in results[:3])
            if is_realtime:
                hint = (
                    f"Low confidence match (best: {top_conf:.2f}). "
                    f"Closest tools returned: {top_names}. "
                    "These tools are a low match. However, you are looking for real-time information. "
                    "You MUST use one of the provided search or browser tools if one is available, otherwise stop."
                )
            else:
                hint = (
                    f"Low confidence match (best: {top_conf:.2f}). "
                    f"Closest tools returned: {top_names}. "
                    "WARNING: These tools are likely IRRELEVANT to your query. "
                    "STOP and do not use them unless you are absolutely sure. "
                    "Consider rephrasing your search or looking for a 'web search' tool instead."
                )

        response = SearchResponse(
            results=results,
            total_tools_indexed=total_tools,
            tokens_saved=tokens_saved,
            tokens_saved_by_dedup=tokens_saved_by_dedup,
            search_time_ms=round(search_time, 2),
            hint=hint,
        )
        # Only cache when no session dedup is active (dedup results are per-agent)
        if not seen_tool_ids:
            self._cache.set(cache_key, response)
        return response

    # Name fragments that indicate a tool can search/browse the web
    _WEB_TOOL_NAMES = {"search", "browse", "browser", "tavily", "web", "lookup", "crawl", "scrape", "fetch"}

    def _run_search(self, query: str, emb_matrix, all_tools, tool_ids, top_k: int, threshold: float,
                    web_boost: float = 0.0, allowed_tool_ids: Optional[set] = None,
                    preference_boosts: Optional[dict[str, float]] = None):
        """Run hybrid search for a given query string. Returns (top_results, SearchResult list).

        web_boost: extra score added to tools whose names contain web/search keywords.
        allowed_tool_ids: if set, only tools with IDs in this set are considered (profile filter).
        preference_boosts: dict of tool_id -> boost amount from agent preferences.
        """
        query_vec = np.array(self.embedder.embed_query(query), dtype=np.float32)
        semantic_scores = emb_matrix @ query_vec
        bm25_scores = self.db.bm25_search(query, limit=50)

        scored_tools = []
        for i, tool in enumerate(all_tools):
            tid = tool_ids[i]
            # Profile filter — skip tools not in the allowed set
            if allowed_tool_ids is not None and tid not in allowed_tool_ids:
                continue
            sem = float(semantic_scores[i])
            bm25 = bm25_scores.get(tid, 0.0)
            hybrid = self.SEMANTIC_WEIGHT * sem + self.BM25_WEIGHT * bm25
            boosted = False
            boost_reason = ""
            
            # Boost web/search tools when caller signals real-time intent
            if web_boost:
                name_lower = all_tools[i].get("name", "").lower()
                category_lower = all_tools[i].get("category", "").lower()
                if any(frag in name_lower for frag in self._WEB_TOOL_NAMES) or category_lower in {"web search", "browser", "search"}:
                    hybrid += web_boost
                    boosted = True
                    boost_reason = "web tool boost"
            
            # Apply agent preference boosts
            if preference_boosts and tid in preference_boosts:
                hybrid += preference_boosts[tid]
                boosted = True
                boost_reason = f"agent preference (+{preference_boosts[tid]:.2f})"
            
            if hybrid >= threshold:
                scored_tools.append((tool, hybrid, sem, bm25, boosted, boost_reason))

        scored_tools.sort(key=lambda x: x[1], reverse=True)
        top_results_raw = scored_tools[:top_k]
        # Keep (tool, score) pairs for callers that use top_results
        top_results = [(t, s) for t, s, *_ in top_results_raw]

        results = []
        seen_names: set[str] = set()
        for tool, confidence, sem, bm25, boosted, boost_reason in top_results_raw:
            name = tool["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            source_info = tool.get("source_info", {})

            # Build a human-readable reason
            reasons = []
            if sem >= 0.5:
                reasons.append(f"strong semantic match ({sem:.2f})")
            elif sem >= 0.3:
                reasons.append(f"semantic match ({sem:.2f})")
            else:
                reasons.append(f"weak semantic match ({sem:.2f})")
            if bm25 > 0:
                reasons.append(f"keyword match (BM25 {bm25:.2f})")
            if boosted:
                if boost_reason:
                    reasons.append(boost_reason)
                else:
                    reasons.append("web/search boost applied (real-time query)")
            match_reason = "; ".join(reasons)

            results.append(SearchResult(
                id=tool["id"],
                name=name,
                description=tool["description"],
                confidence=round(confidence, 4),
                input_schema=tool.get("input_schema", {}),
                source=source_info.get("source_name", "unknown"),
                category=tool.get("category", "Other"),
                how_to_call=self._build_call_instructions(source_info),
                match_reason=match_reason,
            ))
        return top_results, results

    # Stop words to strip when generating keyword-only fallback queries
    _STOP_WORDS = {
        "a", "an", "the", "is", "it", "in", "on", "at", "to", "for",
        "of", "and", "or", "me", "my", "i", "we", "you", "do", "can",
        "how", "what", "when", "where", "who", "which", "that", "this",
        "tell", "give", "get", "find", "show", "please", "want", "need",
        "check", "look", "search", "about", "with", "from", "by", "are",
    }

    def _generate_fallbacks(self, query: str, is_realtime: bool = False) -> list[str]:
        """
        Generate progressively broader reformulations of a query for fallback search.
        Tried in order until one produces a good result.
        """
        words = query.lower().split()
        keywords = [w for w in words if w not in self._STOP_WORDS and len(w) > 2]

        fallbacks = []

        # 1. Just the keywords — strips filler like "tell me about"
        if keywords and keywords != words:
            fallbacks.append(" ".join(keywords))

        # 2. Keywords + "API tool" — shifts embedding toward tool-space
        if keywords:
            fallbacks.append(" ".join(keywords) + " API tool")

        # 3. Rephrase as capability request — only if there are meaningful keywords
        if keywords:
            fallbacks.append(f"capability: {' '.join(keywords[:6])}")

        # 4. "web search <keywords>" — only for real-time queries, not general fallback
        if is_realtime and keywords:
            fallbacks.append(f"web search {' '.join(keywords[:5])}")

        return fallbacks

    # Keywords that signal "I need live/real-time data from the web"
    _REALTIME_KEYWORDS = {
        "price", "prices", "cost", "rate", "rates", "today", "current",
        "live", "now", "latest", "real-time", "realtime", "news", "weather",
        "stock", "crypto", "bitcoin", "btc", "eth", "ethereum", "forex",
        "gold", "silver", "oil", "commodity", "commodities", "usd", "eur",
        "invest", "investment", "market", "trading", "volume", "cap",
        "score", "scores", "breaking", "recent", "right now",
    }

    def _is_realtime_query(self, query: str) -> bool:
        """Return True if the query is asking for live/real-time data from the web."""
        lower = query.lower()
        return any(kw in lower for kw in self._REALTIME_KEYWORDS)

    def _expand_query(self, query: str) -> str:
        """Append web/search terms to steer the embedding toward web-search tools."""
        return query + " browser web search real-time lookup"

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
            skill_name = source_info.get("original_name", server)
            return {
                "type": "skill",
                "skill_name": skill_name,
                "fetch_instructions": f"/v1/skills/{skill_name}",
                "instruction": f"Fetch GET /v1/skills/{skill_name} to get the full SKILL.md instructions, then follow them exactly to complete the task.",
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
