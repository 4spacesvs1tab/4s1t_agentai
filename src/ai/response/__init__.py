"""
Response processing package for the 4S1T Agent AI framework.

This package contains components for parsing, validating, formatting, and handling
AI model responses.
"""

from .processor import (
    ResponseProcessor,
    ProcessedResponse,
    ResponseValidationRule,
    ResponseFormat,
    ValidationResult,
    initialize_default_rules
)

from .fallback import (
    FallbackHandler,
    FallbackStrategy,
    FallbackEvent,
    initialize_default_strategies
)

__all__ = [
    "ResponseProcessor",
    "ProcessedResponse",
    "ResponseValidationRule",
    "ResponseFormat",
    "ValidationResult",
    "initialize_default_rules",
    "FallbackHandler",
    "FallbackStrategy",
    "FallbackEvent",
    "initialize_default_strategies"
]
