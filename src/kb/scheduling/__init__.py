"""kb.scheduling — KB scheduler package. Re-exports KBScheduler for backward compat."""
from kb.scheduling.scheduler import KBScheduler  # noqa: F401

__all__ = ["KBScheduler"]
