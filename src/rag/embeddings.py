"""Embedding service using sentence-transformers for generating vector embeddings."""

import logging
from typing import Optional

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"


class EmbeddingService:
    """Generates text embeddings using the all-MiniLM-L6-v2 sentence-transformers model.

    The model is loaded lazily on first use to avoid unnecessary startup cost.
    """

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: Optional[SentenceTransformer] = None

    def _load_model(self) -> SentenceTransformer:
        """Load the sentence-transformers model on first use."""
        if self._model is None:
            logger.info("Loading embedding model '%s'...", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            logger.info("Embedding model loaded successfully.")
        return self._model

    def embed_text(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text string.

        Args:
            text: The input text to embed.

        Returns:
            A list of floats representing the embedding vector.
        """
        model = self._load_model()
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of text strings.

        Args:
            texts: A list of input texts to embed.

        Returns:
            A list of embedding vectors, one per input text.
        """
        if not texts:
            return []

        model = self._load_model()
        logger.debug("Embedding batch of %d texts.", len(texts))
        embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return embeddings.tolist()
