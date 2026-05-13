"""
Prompt engineering package for the 4S1T Agent AI framework.

This package contains components for prompt template management, validation,
optimization, and versioning.
"""

from .template import (
    PromptTemplate,
    PromptTemplateManager,
    initialize_default_templates
)

__all__ = [
    "PromptTemplate",
    "PromptTemplateManager",
    "initialize_default_templates"
]
