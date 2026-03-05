"""Model registry for provider abstraction."""

from abc import ABC, abstractmethod
from typing import Dict, Optional, List, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import json


class ProviderType:
    """Types of providers supported by the system."""
    NANO_GPT = "nano_gpt"
    BIELIK = "bielik"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass
class ModelInfo:
    """Information about a specific model available from a provider."""
    model_id: str
    name: str
    provider: str
    capabilities: List[str] = field(default_factory=list)
    context_window: int = 4096
    is_pro_model: bool = False
    description: str = ""
    category: str = "general"
    max_tokens: int = 2048
    pricing: Optional[Dict[str, Any]] = None
    is_available: bool = True
    subscription_tier: str = "PRO"  # PRO or FREE
    available_since: Optional[datetime] = None


@dataclass
class ModelPricing:
    """Pricing information for a model."""
    per_token: Optional[float] = None
    per_request: Optional[float] = None
    per_second: Optional[float] = None


class ProviderAdapter(ABC):
    """Abstract base class for provider adapters."""
    
    @abstractmethod
    async def list_models(self) -> List[ModelInfo]:
        """List all available models from this provider."""
        pass
    
    @abstractmethod
    async def get_pricing(self, model_id: str) -> Optional[ModelPricing]:
        """Get pricing information for a specific model."""
        pass
    
    @abstractmethod
    async def is_model_available(self, model_id: str, subscription_tier: str) -> bool:
        """Check if a model is available for the given subscription tier."""
        pass
    
    @abstractmethod
    async def supports_model_listing(self) -> bool:
        """Check if this provider supports model listing APIs."""
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """Get the name of this provider."""
        pass


class ModelProvider:
    """Represents a provider that offers AI models."""
    
    def __init__(self, name: str, provider_type: str, adapter: ProviderAdapter):
        self.name = name
        self.provider_type = provider_type
        self.adapter = adapter
    
    async def get_models(self) -> List[ModelInfo]:
        """Get all models from this provider."""
        return await self.adapter.list_models()
    
    async def get_model_info(self, model_id: str) -> Optional[ModelInfo]:
        """Get information about a specific model."""
        models = await self.adapter.list_models()
        for model in models:
            if model.model_id == model_id:
                return model
        return None
    
    def get_provider_name(self) -> str:
        """Get the name of this provider."""
        return self.name


class ModelRegistry:
    """Registry for managing model providers."""
    
    def __init__(self):
        self.providers: Dict[str, ModelProvider] = {}
    
    def register_provider(self, provider: ModelProvider) -> None:
        """Register a model provider."""
        self.providers[provider.name] = provider
    
    def get_provider(self, name: str) -> Optional[ModelProvider]:
        """Get a provider by name."""
        return self.providers.get(name)
    
    def list_providers(self) -> List[str]:
        """List all registered providers."""
        return list(self.providers.keys())
    
    async def get_all_models(self) -> List[ModelInfo]:
        """Get all models from all registered providers."""
        all_models = []
        for provider_name, provider in self.providers.items():
            try:
                models = await provider.get_models()
                for model in models:
                    model.provider = provider_name  # Ensure provider is set
                all_models.extend(models)
            except Exception as e:
                print(f"Error getting models from provider {provider_name}: {e}")
        return all_models
    
    async def get_model(self, provider_name: str, model_id: str) -> Optional[ModelInfo]:
        """Get a specific model from a provider."""
        provider = self.get_provider(provider_name)
        if provider:
            return await provider.get_model_info(model_id)
        return None
