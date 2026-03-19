"""
embedder.py — Embedding engine for ToolsDNS.

Supports three backends (auto-selected in priority order):
  1. ONNX Runtime (default) — 3-4x faster CPU inference via optimum
  2. sentence-transformers — fallback if ONNX not available
  3. Ollama — local HTTP API for larger/better models

Select the backend via TOOLDNS_EMBEDDING_MODEL:
  - "all-MiniLM-L6-v2"         → ONNX (fast) or sentence-transformers (384d)
  - "bge-base-en-v1.5"         → ONNX or sentence-transformers (768d, recommended)
  - "ollama/nomic-embed-text"   → Ollama (run: ollama serve)
  - "ollama/mxbai-embed-large"  → Ollama large model

All backends expose the same Embedder interface so callers
(search, ingestion) work without any changes.

ONNX vs sentence-transformers produce numerically equivalent
embeddings for the same model — compatible with existing DB vectors.

NOTE: bge-base-en-v1.5 uses instruction-prefixed queries for best
retrieval performance. The Embedder class handles this automatically
via embed_query() for search queries vs embed() for documents.
"""

import numpy as np
from functools import lru_cache
from tooldns.config import settings, logger

_embedder_instance = None


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class _ONNXBackend:
    """
    Fast local embedding via ONNX Runtime (optimum).

    3-4x faster than sentence-transformers on CPU for the same model.
    Exports the HF model to ONNX on first use (cached to disk after).
    Produces numerically equivalent embeddings — compatible with existing
    DB vectors indexed by sentence-transformers.

    Requires: pip install optimum[onnxruntime] onnxruntime
    """

    # Map short names to full HF repo IDs (needed so the local cache is found)
    _HF_ALIASES = {
        "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
        "all-mpnet-base-v2": "sentence-transformers/all-mpnet-base-v2",
        "bge-base-en-v1.5": "BAAI/bge-base-en-v1.5",
    }

    def __init__(self, model_name: str):
        self.model_name = self._HF_ALIASES.get(model_name, model_name)
        self._model = None
        self._tokenizer = None

    def _load(self):
        if self._model is not None:
            return
        logger.info(f"Loading ONNX embedding model: {self.model_name}")
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_name, local_files_only=True
        )
        self._model = ORTModelForFeatureExtraction.from_pretrained(
            self.model_name, export=True, local_files_only=True
        )
        logger.info("ONNX embedding model loaded")

    def _pool_and_normalize(self, last_hidden: np.ndarray, attention_mask: np.ndarray) -> list[list[float]]:
        mask = attention_mask[..., None].astype(np.float32)
        pooled = (last_hidden * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)
        norms = np.linalg.norm(pooled, axis=-1, keepdims=True).clip(min=1e-9)
        return (pooled / norms).tolist()

    @lru_cache(maxsize=256)
    def embed(self, text: str) -> list[float]:
        self._load()
        inputs = self._tokenizer(text, return_tensors="np", padding=True,
                                 truncation=True, max_length=512)
        outputs = self._model(**inputs)
        return self._pool_and_normalize(
            outputs.last_hidden_state, inputs["attention_mask"]
        )[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self._load()
        inputs = self._tokenizer(texts, return_tensors="np", padding=True,
                                 truncation=True, max_length=512)
        outputs = self._model(**inputs)
        return self._pool_and_normalize(
            outputs.last_hidden_state, inputs["attention_mask"]
        )

    def preload(self):
        self._load()
        self.embed("warmup")
        logger.info("ONNX embedding model ready")


class _SentenceTransformerBackend:
    """Local embedding via sentence-transformers (fallback if ONNX unavailable)."""

    # Map short names to full HF repo IDs
    _HF_ALIASES = {
        "bge-base-en-v1.5": "BAAI/bge-base-en-v1.5",
        "bge-large-en-v1.5": "BAAI/bge-large-en-v1.5",
        "bge-small-en-v1.5": "BAAI/bge-small-en-v1.5",
    }

    def __init__(self, model_name: str):
        self.model_name = self._HF_ALIASES.get(model_name, model_name)
        self._model = None

    def _load(self):
        if self._model is None:
            logger.info(f"Loading sentence-transformer model: {self.model_name}")
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            logger.info("Embedding model loaded successfully")

    @lru_cache(maxsize=256)
    def embed(self, text: str) -> list[float]:
        self._load()
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self._load()
        return [e.tolist() for e in self._model.encode(texts, normalize_embeddings=True)]

    def preload(self):
        self._load()


class _OllamaBackend:
    """
    Embedding via Ollama local HTTP API.

    Use this for larger/better models like nomic-embed-text or
    mxbai-embed-large. Requires Ollama to be running:
        ollama serve
        ollama pull nomic-embed-text

    Note: Ollama's /api/embeddings endpoint is single-input only,
    so embed_batch() makes sequential HTTP calls. This is slower
    than sentence-transformers batching but acceptable for ingestion.
    """

    def __init__(self, model_name: str, base_url: str = "http://localhost:11434"):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self._check_connection()

    def _check_connection(self):
        import httpx
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            logger.info(f"Ollama connected at {self.base_url}, model: {self.model_name}")
        except Exception as e:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.base_url}: {e}\n"
                f"Make sure Ollama is running: 'ollama serve'\n"
                f"And the model is pulled: 'ollama pull {self.model_name}'"
            )

    @lru_cache(maxsize=256)
    def embed(self, text: str) -> list[float]:
        import httpx
        resp = httpx.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.model_name, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Ollama doesn't support batch — sequential calls
        return [self.embed(t) for t in texts]

    def preload(self):
        # Warm up with a short string to verify model is loaded
        self.embed("warmup")
        logger.info(f"Ollama model '{self.model_name}' ready")


# ---------------------------------------------------------------------------
# Public Embedder class
# ---------------------------------------------------------------------------

class Embedder:
    """
    Generates semantic embeddings for tool descriptions.

    Selects the backend automatically based on settings.embedding_model:
    - Names starting with "ollama/" use the Ollama backend.
    - All other names use sentence-transformers.

    The public embed() and embed_batch() interface is identical
    regardless of which backend is active.

    Use embed() / embed_batch() for documents (tool descriptions).
    Use embed_query() for search queries — adds instruction prefix
    for models that benefit from it (e.g., bge-base-en-v1.5).

    Attributes:
        model_name: Full model name including backend prefix (e.g., "ollama/nomic-embed-text").
    """

    # Models that benefit from an instruction prefix on queries
    _QUERY_PREFIX_MODELS = {
        "bge-base-en-v1.5", "BAAI/bge-base-en-v1.5",
        "bge-large-en-v1.5", "BAAI/bge-large-en-v1.5",
        "bge-small-en-v1.5", "BAAI/bge-small-en-v1.5",
    }
    _QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str = None):
        raw_name = model_name or settings.embedding_model
        self.model_name = raw_name
        self._uses_query_prefix = raw_name in self._QUERY_PREFIX_MODELS

        if raw_name.startswith("ollama/"):
            ollama_model = raw_name[len("ollama/"):]
            self._backend = _OllamaBackend(ollama_model)
        else:
            # Try ONNX first (3-4x faster on CPU); fall back to sentence-transformers
            try:
                import optimum.onnxruntime  # noqa: F401
                self._backend = _ONNXBackend(raw_name)
                logger.info(f"Embedding backend: ONNX ({raw_name})")
            except ImportError:
                self._backend = _SentenceTransformerBackend(raw_name)
                logger.info(f"Embedding backend: sentence-transformers ({raw_name})")

        if self._uses_query_prefix:
            logger.info(f"Query prefix enabled for model {raw_name}")

    def embed(self, text: str) -> list[float]:
        """Generate an embedding for a document (tool description). No prefix added."""
        return self._backend.embed(text)

    def embed_query(self, text: str) -> list[float]:
        """Generate an embedding for a search query. Adds instruction prefix for BGE models."""
        if self._uses_query_prefix:
            text = self._QUERY_PREFIX + text
        return self._backend.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of documents."""
        return self._backend.embed_batch(texts)

    def preload(self):
        """
        Explicitly load/warm-up the model at server startup.

        Call this during startup to avoid first-request latency.
        sentence-transformers takes ~2s to load; Ollama warmup is instant.
        """
        self._backend.preload()


def get_embedder() -> Embedder:
    """
    Get the global singleton Embedder instance.

    Returns the same instance on every call so the model is only
    loaded once per process.

    Returns:
        Embedder: The shared embedder instance.
    """
    global _embedder_instance
    if _embedder_instance is None:
        _embedder_instance = Embedder()
    return _embedder_instance
