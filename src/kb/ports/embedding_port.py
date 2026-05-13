"""
EmbeddingPort — domain interface for text embedding.

Rule: this file must never import httpx, sqlite3, os.environ, or any I/O
library.  Only standard-library ABCs are allowed here.
"""
from abc import ABC, abstractmethod


class EmbeddingPort(ABC):
    """Abstract embedding service.

    Implementations live in src/infrastructure/embedding/.
    Wire a concrete adapter at the composition root (initializer / runner).
    """

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            List of float vectors, one per input text.

        Raises:
            EmbeddingError: If the embedding provider fails after all retries.
        """
        ...
