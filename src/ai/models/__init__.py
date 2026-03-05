"""
AI models package for the 4S1T Agent AI framework.

This package contains implementations for various AI models and the model management system.
"""

from .base import (
    BaseModel,
    ModelManager,
    ModelMetadata,
    ModelResponse,
    ModelStatus,
    ModelType
)

from .language_model import (
    MockLanguageModel,
    OpenAILanguageModel,
    create_language_model
)

from .nano_gpt import (
    NanoGPTLanguageModel,
    create_nano_gpt_model
)

from .selection import (
    ModelSelectionService,
    TaskType,
    TaskRequirements,
    get_model_selection_service
)

__all__ = [
    "BaseModel",
    "ModelManager",
    "ModelMetadata",
    "ModelResponse",
    "ModelStatus",
    "ModelType",
    "MockLanguageModel",
    "OpenAILanguageModel",
    "NanoGPTLanguageModel",
    "create_language_model",
    "create_nano_gpt_model",
    "ModelSelectionService",
    "TaskType",
    "TaskRequirements",
    "get_model_selection_service"
]
