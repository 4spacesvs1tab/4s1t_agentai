"""
KB domain exceptions.

EmbeddingError is the canonical exception for embedding API failures.
It is defined in core.exceptions (as a TransientError subclass) and
re-exported here for backward compatibility with existing imports.

All new KB exception types should be imported directly from core.exceptions.
"""
from core.exceptions import EmbeddingError as EmbeddingError  # noqa: F401

__all__ = ["EmbeddingError"]
