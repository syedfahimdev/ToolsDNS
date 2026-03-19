"""
cache.py — Multi-level caching for ToolsDNS.

L1: In-memory LRU (always active)
L2: Redis (optional, activated by TOOLDNS_REDIS_URL env var)

Usage:
    from tooldns.cache import create_cache
    cache = create_cache()  # Auto-detects Redis availability
"""

import json
import time
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, Optional

from tooldns.config import logger, settings
from tooldns.models import SearchResponse

try:
    import redis as _redis_lib
except ImportError:
    _redis_lib = None


class CacheLayer(ABC):
    """Abstract base class for cache implementations."""

    @abstractmethod
    def get(self, key: tuple) -> Optional[SearchResponse]:
        """Retrieve a cached SearchResponse by key, or None if miss."""

    @abstractmethod
    def set(self, key: tuple, value: SearchResponse, ttl: float = 0) -> None:
        """Store a SearchResponse under key with optional TTL in seconds."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all entries from this cache layer."""

    @property
    @abstractmethod
    def stats(self) -> dict:
        """Return hit/miss/size statistics."""


class MemoryCache(CacheLayer):
    """
    In-memory LRU cache with TTL expiry.

    Thread-safe OrderedDict implementation — same pattern as _SearchCache
    but conforms to the CacheLayer interface.
    """

    def __init__(self, maxsize: int = 256, ttl_secs: float = 60.0):
        self._cache: OrderedDict[tuple, tuple] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl_secs
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: tuple) -> Optional[SearchResponse]:
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
            self._cache.move_to_end(key)
            self._hits += 1
            return response

    def set(self, key: tuple, value: SearchResponse, ttl: float = 0) -> None:
        effective_ttl = ttl if ttl > 0 else self._ttl
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (time.monotonic() + effective_ttl, value)
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
                "layer": "memory",
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
                "size": len(self._cache),
                "maxsize": self._maxsize,
                "ttl_secs": self._ttl,
            }


class RedisCache(CacheLayer):
    """
    Redis-backed cache layer (L2).

    Uses Pydantic's model_dump_json/model_validate_json for serialization.
    Gracefully falls back to miss on any Redis error — never crashes.
    """

    KEY_PREFIX = "tooldns:cache:"

    def __init__(self, redis_url: str, ttl_secs: float = 60.0):
        self._ttl = ttl_secs
        self._hits = 0
        self._misses = 0
        self._errors = 0
        self._client = None
        if _redis_lib is None:
            logger.warning("redis-py not installed — RedisCache disabled")
            return
        try:
            self._client = _redis_lib.from_url(redis_url, decode_responses=True)
            self._client.ping()
            logger.info(f"Redis cache connected: {redis_url}")
        except Exception as e:
            logger.warning(f"Redis connection failed, L2 cache disabled: {e}")
            self._client = None

    def _make_key(self, key: tuple) -> str:
        """Convert tuple cache key to a Redis string key."""
        # Serialize tuple elements to a stable string
        parts = []
        for k in key:
            if isinstance(k, frozenset):
                parts.append(f"fs:{','.join(sorted(str(x) for x in k))}")
            else:
                parts.append(str(k))
        return self.KEY_PREFIX + "|".join(parts)

    def get(self, key: tuple) -> Optional[SearchResponse]:
        if not self._client:
            self._misses += 1
            return None
        try:
            raw = self._client.get(self._make_key(key))
            if raw is None:
                self._misses += 1
                return None
            self._hits += 1
            return SearchResponse.model_validate_json(raw)
        except Exception as e:
            logger.debug(f"Redis get error: {e}")
            self._errors += 1
            self._misses += 1
            return None

    def set(self, key: tuple, value: SearchResponse, ttl: float = 0) -> None:
        if not self._client:
            return
        effective_ttl = ttl if ttl > 0 else self._ttl
        try:
            raw = value.model_dump_json()
            self._client.setex(self._make_key(key), int(effective_ttl), raw)
        except Exception as e:
            logger.debug(f"Redis set error: {e}")
            self._errors += 1

    def clear(self) -> None:
        if not self._client:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = self._client.scan(cursor, match=f"{self.KEY_PREFIX}*", count=100)
                if keys:
                    self._client.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.debug(f"Redis clear error: {e}")
            self._errors += 1

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "layer": "redis",
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
            "errors": self._errors,
            "connected": self._client is not None,
            "ttl_secs": self._ttl,
        }


class CompositeCache(CacheLayer):
    """
    Two-level cache: L1 (memory) → L2 (redis).

    Read: check L1 first, then L2. On L2 hit, backfill L1.
    Write: write-through to both layers.
    """

    def __init__(self, l1: MemoryCache, l2: RedisCache):
        self._l1 = l1
        self._l2 = l2

    def get(self, key: tuple) -> Optional[SearchResponse]:
        # Check L1
        result = self._l1.get(key)
        if result is not None:
            return result
        # Check L2
        result = self._l2.get(key)
        if result is not None:
            # Backfill L1
            self._l1.set(key, result)
            return result
        return None

    def set(self, key: tuple, value: SearchResponse, ttl: float = 0) -> None:
        self._l1.set(key, value, ttl=ttl)
        self._l2.set(key, value, ttl=ttl)

    def clear(self) -> None:
        self._l1.clear()
        self._l2.clear()

    @property
    def stats(self) -> dict:
        l1_stats = self._l1.stats
        l2_stats = self._l2.stats
        total_hits = l1_stats["hits"] + l2_stats["hits"]
        total_misses = l2_stats["misses"]  # Only L2 misses = true misses
        total = total_hits + total_misses
        return {
            "layer": "composite",
            "hits": total_hits,
            "misses": total_misses,
            "hit_rate": round(total_hits / total, 3) if total else 0.0,
            "l1": l1_stats,
            "l2": l2_stats,
        }


def create_cache(maxsize: int = 256, ttl_secs: float = 60.0) -> CacheLayer:
    """
    Factory: create the best available cache.

    Returns CompositeCache (memory + redis) if TOOLDNS_REDIS_URL is set
    and redis-py is installed. Otherwise returns MemoryCache only.
    """
    l1 = MemoryCache(maxsize=maxsize, ttl_secs=ttl_secs)

    redis_url = settings.redis_url
    if redis_url and _redis_lib is not None:
        l2 = RedisCache(redis_url=redis_url, ttl_secs=ttl_secs)
        if l2._client is not None:
            logger.info("Cache: L1 (memory) + L2 (redis)")
            return CompositeCache(l1, l2)
        logger.info("Cache: L1 (memory) only — Redis connection failed")
    else:
        if redis_url and _redis_lib is None:
            logger.info("Cache: L1 (memory) only — redis-py not installed")
        else:
            logger.info("Cache: L1 (memory) only")

    return l1
