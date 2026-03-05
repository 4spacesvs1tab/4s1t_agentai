"""Nano-GPT provider adapter for the model registry."""

import asyncio
import logging
import os
from typing import Dict, List, Optional, Any
from datetime import datetime

from .model_registry import ProviderAdapter, ModelInfo, ModelPricing, ProviderType
from ..models.nano_gpt import NanoGPTLanguageModel
from ..models.base import ModelMetadata, ModelType
from ..models.nano_gpt_api import NanoGPTApiClient

logger = logging.getLogger(__name__)


class NanoGPTProvider(ProviderAdapter):
    """Provider adapter for Nano-GPT.com API."""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the Nano-GPT provider adapter.
        
        Args:
            api_key: API key for Nano-GPT (if not provided, uses NANO_GPT_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("NANO_GPT_API_KEY")
        self.base_url = "https://nano-gpt.com/api"
        self.cached_models: Optional[List[ModelInfo]] = None
        self.cache_timestamp: Optional[datetime] = None
        self.cache_ttl = 300  # 5 minutes cache TTL
        self.api_client = NanoGPTApiClient(self.api_key, self.base_url) if self.api_key else None
    
    async def list_models(self) -> List[ModelInfo]:
        """List all available models from Nano-GPT provider by fetching from API."""
        try:
            # Check cache validity
            if (self.cached_models and self.cache_timestamp and 
                (datetime.now() - self.cache_timestamp).total_seconds() < self.cache_ttl):
                logger.debug("Returning cached models list")
                return self.cached_models
            
            # If no API key is available, return empty list
            if not self.api_key or not self.api_client:
                logger.warning("No API key available for Nano-GPT provider")
                return []
            
            # Fetch models from API
            logger.info("Fetching models from Nano-GPT API...")
            models_data = await self.api_client.list_models(detailed=True)
            
            # Parse the models data
            models = []
            
            # The API response should have a 'data' field with model information
            if 'data' in models_data:
                for model_entry in models_data['data']:
                    # Extract model information
                    model_id = model_entry.get('id', '')
                    model_name = model_entry.get('name', model_id)
                    description = model_entry.get('description', '')
                    context_window = model_entry.get('context_window', 4096)
                    owned_by = model_entry.get('owned_by', '')
                    created = model_entry.get('created', '')
                    
                    # Determine if this is a PRO model based on ownership or other criteria
                    # For now, we'll assume most models are PRO tier
                    is_pro_model = True
                    subscription_tier = "PRO"
                    
                    # Some basic categorization based on model name
                    category = "General"
                    model_type = "Text"
                    capabilities = ["text_generation"]
                    
                    # Determine model type based on name patterns
                    if any(keyword in model_id.lower() for keyword in ['vision', 'vl', 'image', 'img', 'v1', 'v2', 'v3']):
                        model_type = "Image"
                        capabilities.append("image_processing")
                    elif any(keyword in model_id.lower() for keyword in ['video', 'vl', 'multimodal']):
                        model_type = "Video"
                        capabilities.append("video_processing")
                    
                    # Categorize models based on name patterns
                    if any(keyword in model_id.lower() for keyword in ['glm', 'z-ai']):
                        if '4.6' in model_id:
                            category = "Top Models"
                        elif any(keyword in model_id.lower() for keyword in ['coder', 'code']):
                            category = "Coding"
                        else:
                            category = "Deep Research"
                    elif any(keyword in model_id.lower() for keyword in ['deepseek', 'deepseek-r1']):
                        if 'chat' in model_id.lower():
                            category = "Top Models"
                        elif any(keyword in model_id.lower() for keyword in ['coder', 'code']):
                            category = "Coding"
                        elif 'math' in model_id.lower():
                            category = "Math"
                        else:
                            category = "Deep Research"
                    elif any(keyword in model_id.lower() for keyword in ['qwen', 'qwq']):
                        if any(keyword in model_id.lower() for keyword in ['coder', 'code']):
                            category = "Coding"
                        elif 'math' in model_id.lower():
                            category = "Math"
                        else:
                            category = "General"
                    elif any(keyword in model_id.lower() for keyword in ['claude', 'sonnet']):
                        category = "Top Models"
                    elif any(keyword in model_id.lower() for keyword in ['gpt', 'o1', 'o3', 'o4']):
                        category = "Top Models"
                    elif any(keyword in model_id.lower() for keyword in ['gemini', 'learnlm']):
                        category = "Top Models"
                    elif any(keyword in model_id.lower() for keyword in ['math', 'reasoner', 'reasoning']):
                        category = "Math"
                    elif any(keyword in model_id.lower() for keyword in ['coder', 'code', 'coding']):
                        category = "Coding"
                    elif any(keyword in model_id.lower() for keyword in ['story', 'roleplay', 'rp']):
                        category = "Roleplay/storytelling models"
                    elif any(keyword in model_id.lower() for keyword in ['uncensored', 'venice']):
                        category = "Uncensored"
                    elif any(keyword in model_id.lower() for keyword in ['tee']):
                        category = "Private/TEE"
                    else:
                        category = "More"
                    
                    # Special handling for certain models
                    if 'thinking' in model_id.lower():
                        category = "Deep Research"
                    
                    # Create ModelInfo object
                    model_info = ModelInfo(
                        model_id=model_id,
                        name=model_name,
                        provider="nano_gpt",
                        capabilities=capabilities,
                        context_window=context_window,
                        is_pro_model=is_pro_model,
                        description=description,
                        category=category,
                        max_tokens=min(context_window, 4096),  # Set reasonable max tokens
                        subscription_tier=subscription_tier,
                        available_since=datetime.now() if created else None
                    )
                    models.append(model_info)
            
            # Cache the results
            self.cached_models = models
            self.cache_timestamp = datetime.now()
            
            logger.info(f"Listed {len(models)} models from Nano-GPT provider")
            return models
            
        except Exception as e:
            logger.error(f"Error listing Nano-GPT models: {e}")
            # Return empty list on error
            return []
    
    async def get_pricing(self, model_id: str) -> Optional[ModelPricing]:
        """Get pricing information for a specific model."""
        try:
            # Nano-GPT pricing is typically per-token
            # TODO: Fetch real pricing from API if available
            
            # Since we're now fetching models dynamically, we'll use a more general approach
            # Most Nano-GPT models are PRO tier with similar pricing
            return ModelPricing(
                per_token=0.00002,  # $0.00002 per token (~$0.002 per 100 tokens)
                per_request=0.001,  # $0.001 per request
                per_second=None
            )
                
        except Exception as e:
            logger.error(f"Error getting pricing for model {model_id}: {e}")
            return None
    
    async def is_model_available(self, model_id: str, subscription_tier: str) -> bool:
        """Check if a model is available for the given subscription tier."""
        try:
            # First, ensure we have the latest model list
            models = await self.list_models()
            
            # Find the model in our list
            model_exists = any(model.model_id == model_id for model in models)
            
            if not model_exists:
                return False
                
            # Check subscription tier
            if subscription_tier == "PRO":
                # PRO subscription gets access to all models
                return True
            elif subscription_tier == "FREE":
                # For now, we'll assume FREE tier has limited access
                # In the future, we might want to check specific model properties
                return any(model.model_id == model_id and model.subscription_tier == "FREE" for model in models)
            else:
                logger.warning(f"Unknown subscription tier: {subscription_tier}")
                return False
                
        except Exception as e:
            logger.error(f"Error checking model availability for {model_id}: {e}")
            return False
    
    async def supports_model_listing(self) -> bool:
        """Check if this provider supports model listing APIs."""
        # Nano-GPT supports listing through its model catalog
        return True
    
    def get_provider_name(self) -> str:
        """Get the name of this provider."""
        return "nano_gpt"
    
    async def close(self) -> None:
        """Close the provider and release resources."""
        if self.api_client:
            await self.api_client.close()
            self.api_client = None


async def create_nanogpt_model(model_id: str, api_key: Optional[str] = None) -> Optional[NanoGPTLanguageModel]:
    """
    Factory function to create a NanoGPTLanguageModel instance.
    
    Args:
        model_id: The model ID to create
        api_key: Optional API key (uses env var if not provided)
        
    Returns:
        NanoGPTLanguageModel instance or None if creation fails
    """
    try:
        # Create model metadata
        metadata = ModelMetadata(
            name=f"nano-gpt-{model_id}",
            version="1.0",
            model_type=ModelType.LANGUAGE_MODEL,
            description=f"Nano-GPT {model_id} model",
            config={
                "base_url": "https://nano-gpt.com/api",
                "api_endpoint": "/v1/chat/completions",
                "default_model": model_id,
                "subscription_tier": "PRO"
            }
        )
        
        # Create the model instance
        api_key = api_key or os.getenv("NANO_GPT_API_KEY")
        model = NanoGPTLanguageModel(metadata, api_key=api_key)
        
        # Load the model
        success = await model.load()
        if not success:
            logger.error(f"Failed to load Nano-GPT model: {model_id}")
            return None
        
        logger.info(f"Created Nano-GPT model: {model_id}")
        return model
        
    except Exception as e:
        logger.error(f"Error creating Nano-GPT model {model_id}: {e}")
        return None
