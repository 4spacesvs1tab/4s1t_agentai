"""
Core exception hierarchy for the agent system.

Hierarchy:
    AgentError
    ├── TransientError   — safe to retry (network timeouts, rate limits)
    │   ├── EmbeddingError
    │   └── IngestionError
    └── PermanentError   — do not retry (invalid config, auth failure)
        ├── ConfigError
        └── AuthError

Usage:
    Catch TransientError to apply a generic retry policy.
    Catch the specific subclass (EmbeddingError, IngestionError) for
    targeted handling (e.g. log + skip item vs. log + skip domain).

    Permanent errors should be surfaced immediately — retrying will not help.
"""


class AgentError(Exception):
    """Base class for all agent system exceptions."""


class TransientError(AgentError):
    """Transient failure — caller may retry after a delay."""


class PermanentError(AgentError):
    """Permanent failure — retrying will not help."""


class EmbeddingError(TransientError):
    """
    Raised when the embedding API fails after all retry attempts.

    Callers must not store partial or zero-vector data on this exception.
    The caller decides the retry/skip policy (skip item, alert operator, etc.).
    """


class IngestionError(TransientError):
    """
    Raised when content ingestion fails for a domain or individual item.

    When raised from the scheduler-level dispatch, the scheduler logs at
    WARNING level and continues to the next domain — the failure is
    domain-scoped, not system-wide.
    """


class ConfigError(PermanentError):
    """
    Raised when required configuration is invalid, missing, or unparseable.

    Do not retry — the operator must fix the configuration.
    """


class AuthError(PermanentError):
    """
    Raised when authentication or authorisation fails.

    Do not retry — credentials must be rotated or fixed by the operator.
    """
