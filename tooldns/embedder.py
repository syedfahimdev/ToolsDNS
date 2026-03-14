"""
embedder.py — Embedding engine for ToolDNS.

Generates vector embeddings from tool descriptions using sentence-transformers.
These embeddings enable semantic search — finding tools by meaning, not keywords.

The default model (all-MiniLM-L6-v2) is:
    - ~23MB in size
    - Produces 384-dimensional vectors
    - Runs 100% locally on CPU (no API calls, no cost)
    - Fast enough for real-time search (<10ms per embedding)

Usage:
    from tooldns.embedder import Embedder
    embedder = Embedder()
    vector = embedder.embed("create a github issue")
    vectors = embedder.embed_batch(["tool 1 description", "tool 2 description"])
"""

from tooldns.config import settings, logger

_embedder_instance = None


class Embedder:
    """
    Generates semantic embeddings for tool descriptions.

    Uses sentence-transformers to convert text into fixed-size
    float vectors. These vectors capture the semantic meaning
    of the text, enabling similarity search.

    The model is loaded lazily on first use and cached in memory
    for subsequent calls. This avoids the ~2 second load time
    on every embedding request.

    Attributes:
        model_name: Name of the sentence-transformer model.
        model: The loaded SentenceTransformer model instance.
    """

    def __init__(self, model_name: str = None):
        """
        Initialize the embedder with a specific model.

        The model is NOT loaded here — it's loaded lazily on first
        embed() call. This keeps server startup fast.

        Args:
            model_name: Sentence-transformer model name.
                        Default: settings.embedding_model (all-MiniLM-L6-v2).
        """
        self.model_name = model_name or settings.embedding_model
        self.model = None

    def _load_model(self):
        """
        Lazy-load the embedding model into memory.

        Downloads the model on first run (~23MB) and caches it locally.
        Subsequent loads use the cached version (~200ms).
        """
        if self.model is None:
            logger.info(f"Loading embedding model: {self.model_name}")
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(self.model_name)
            logger.info("Embedding model loaded successfully")

    def embed(self, text: str) -> list[float]:
        """
        Generate an embedding vector for a single text string.

        Converts the input text into a 384-dimensional float vector
        that captures its semantic meaning. Normalized so cosine
        similarity can be computed as a simple dot product.

        Args:
            text: The text to embed (e.g., a tool description).

        Returns:
            list[float]: 384-dimensional embedding vector.
        """
        self._load_model()
        return self.model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple texts in a single batch.

        More efficient than calling embed() in a loop because
        sentence-transformers batches the computation internally.

        Args:
            texts: List of texts to embed.

        Returns:
            list[list[float]]: List of embedding vectors, one per input text.
        """
        self._load_model()
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return [e.tolist() for e in embeddings]

    def preload(self):
        """
        Explicitly load the model into memory.

        Call this during server startup to avoid the first-request
        latency hit. The model takes ~2 seconds to load.
        """
        self._load_model()


def get_embedder() -> Embedder:
    """
    Get the global singleton Embedder instance.

    Creates the instance on first call and returns the cached
    instance on subsequent calls. This ensures only one copy
    of the model is ever loaded into memory.

    Returns:
        Embedder: The shared embedder instance.
    """
    global _embedder_instance
    if _embedder_instance is None:
        _embedder_instance = Embedder()
    return _embedder_instance
