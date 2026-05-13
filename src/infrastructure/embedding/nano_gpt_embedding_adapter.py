"""
NanoGptEmbeddingAdapter — infrastructure adapter for the nano-gpt embeddings endpoint.

Implements EmbeddingPort using synchronous httpx.
Retry logic (3 attempts, 1 s / 2 s backoff) is preserved verbatim from
the original preprocessor._embed_texts() implementation.
"""
import time

import logging

import httpx

from kb.exceptions import EmbeddingError
from kb.ports.embedding_port import EmbeddingPort

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "BAAI/bge-m3"
_RETRY_DELAYS = (1, 2)  # seconds between attempts 1→2 and 2→3


class NanoGptEmbeddingAdapter(EmbeddingPort):
    """Calls the nano-gpt /embeddings endpoint to produce bge-m3 vectors."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._url = f"{base_url}/embeddings"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one 1024-dim float vector per input text.

        Retries up to 3 times with exponential backoff (1 s, 2 s).
        Raises EmbeddingError on final failure — never returns zero vectors.
        """
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = httpx.post(
                    self._url,
                    json={"model": _EMBEDDING_MODEL, "input": texts},
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=60.0,
                )
                resp.raise_for_status()
                data = resp.json()
                return [item["embedding"] for item in data["data"]]
            except Exception as exc:
                last_exc = exc
                if attempt < len(_RETRY_DELAYS):
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "Embedding attempt %d/3 failed: %s — retrying in %ds",
                        attempt + 1, exc, delay,
                    )
                    time.sleep(delay)

        logger.error("Embedding API failed after 3 attempts: %s", last_exc)
        raise EmbeddingError(
            f"Embedding API failed after 3 attempts: {last_exc}"
        ) from last_exc
