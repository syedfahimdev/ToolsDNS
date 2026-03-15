"""
embedder.py — Embedding engine for ToolDNS.

Supports two backends:
  1. sentence-transformers (default) — fully local, no extra setup
  2. Ollama — local HTTP API, supports larger/better models

Select the backend via TOOLDNS_EMBEDDING_MODEL:
  - "all-MiniLM-L6-v2"         → sentence-transformers (default)
  - "ollama/nomic-embed-text"   → Ollama (run: ollama serve)
  - "ollama/mxbai-embed-large"  → Ollama large model

Both backends expose the same Embedder interface so all callers
(search, ingestion) work without any changes.
"""

from functools import lru_cache
from tooldns.config import settings, logger

_embedder_instance = None


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class _SentenceTransformerBackend:
    """Local embedding via sentence-transformers (default)."""

    def __init__(self, model_name: str):
        self.model_name = model_name
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

    Attributes:
        model_name: Full model name including backend prefix (e.g., "ollama/nomic-embed-text").
    """

    def __init__(self, model_name: str = None):
        raw_name = model_name or settings.embedding_model
        self.model_name = raw_name

        if raw_name.startswith("ollama/"):
            ollama_model = raw_name[len("ollama/"):]
            self._backend = _OllamaBackend(ollama_model)
        else:
            self._backend = _SentenceTransformerBackend(raw_name)

    def embed(self, text: str) -> list[float]:
        """Generate an embedding for a single text string."""
        return self._backend.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
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
