"""
Language model implementations for the 4S1T Agent AI framework.

This module provides concrete implementations of language models, including
mock models for testing and integration with popular language model APIs.
"""

import asyncio
import json
import logging
import random
from typing import Any, Dict, List, Optional, Union
from datetime import datetime

from .base import BaseModel, ModelMetadata, ModelResponse, ModelStatus, ModelType


logger = logging.getLogger(__name__)


class MockLanguageModel(BaseModel):
    """
    Mock language model for testing and development.
    
    This model simulates language model responses without actually loading
    a real model, useful for development and testing purposes.
    """
    
    def __init__(self, metadata: ModelMetadata):
        """
        Initialize the mock language model.
        
        Args:
            metadata: Metadata describing the model
        """
        super().__init__(metadata)
        self.response_templates = [
            "I understand your question about {topic}. Here's what I think...",
            "That's an interesting point regarding {topic}. Based on my knowledge...",
            "Regarding {topic}, I would suggest considering several factors...",
            "Your query about {topic} touches on important concepts...",
            "After analyzing {topic}, I've concluded that..."
        ]
        self.topics = ["AI", "technology", "software development", "machine learning", "data science"]
    
    async def load(self) -> bool:
        """
        Load the mock model (simulated).
        
        Returns:
            bool: Always returns True for mock model
        """
        try:
            # Simulate loading time
            await asyncio.sleep(0.1)
            self.logger.info(f"Mock model {self.metadata.name} loaded")
            return True
        except Exception as e:
            self.logger.error(f"Failed to load mock model {self.metadata.name}: {e}")
            return False
    
    async def unload(self) -> bool:
        """
        Unload the mock model (simulated).
        
        Returns:
            bool: Always returns True for mock model
        """
        try:
            # Simulate unloading time
            await asyncio.sleep(0.05)
            self.logger.info(f"Mock model {self.metadata.name} unloaded")
            return True
        except Exception as e:
            self.logger.error(f"Failed to unload mock model {self.metadata.name}: {e}")
            return False
    
    async def generate(self, prompt: Union[str, Dict[str, Any]], **kwargs) -> ModelResponse:
        """
        Generate a response from the mock model.
        
        Args:
            prompt: The input prompt
            **kwargs: Additional arguments for generation
            
        Returns:
            ModelResponse: A simulated model response
        """
        start_time = datetime.now()
        
        try:
            # Simulate processing time
            await asyncio.sleep(random.uniform(0.1, 0.5))
            
            # Extract topic from prompt or use random
            if isinstance(prompt, str):
                topic = prompt.split()[0] if prompt.split() else random.choice(self.topics)
            else:
                topic = str(prompt)[:20]  # Simple extraction for dict prompts
            
            # Select a random template and fill it
            template = random.choice(self.response_templates)
            response_content = template.format(topic=topic)
            
            # Add some variation based on parameters
            if kwargs.get('temperature', 1.0) > 0.8:
                response_content += " This is a highly creative response!"
            elif kwargs.get('temperature', 1.0) < 0.3:
                response_content += " This is a very precise response."
            
            end_time = datetime.now()
            latency_ms = (end_time - start_time).total_seconds() * 1000
            
            return ModelResponse(
                content=response_content,
                metadata={
                    "model_type": "mock",
                    "temperature": kwargs.get('temperature', 1.0),
                    "max_tokens": kwargs.get('max_tokens', 100),
                },
                model_name=self.metadata.name,
                latency_ms=latency_ms
            )
        except Exception as e:
            self.logger.error(f"Error generating response from mock model: {e}")
            end_time = datetime.now()
            latency_ms = (end_time - start_time).total_seconds() * 1000
            
            return ModelResponse(
                content=f"Error generating response: {str(e)}",
                metadata={"error": True, "exception": str(e)},
                model_name=self.metadata.name,
                latency_ms=latency_ms
            )
    
    def get_info(self) -> Dict[str, Any]:
        """
        Get information about the mock model.
        
        Returns:
            Dict[str, Any]: Dictionary containing model information
        """
        return {
            "name": self.metadata.name,
            "version": self.metadata.version,
            "type": self.metadata.model_type.value,
            "status": self.status.value,
            "description": self.metadata.description,
            "capabilities": ["text_generation", "conversation_simulation"],
            "is_mock": True
        }


class OpenAILanguageModel(BaseModel):
    """
    OpenAI language model integration.
    
    This model integrates with OpenAI's API for language model capabilities.
    Note: Requires openai package and API key configuration.
    """
    
    def __init__(self, metadata: ModelMetadata, api_key: Optional[str] = None):
        """
        Initialize the OpenAI language model.
        
        Args:
            metadata: Metadata describing the model
            api_key: OpenAI API key (can also be set via environment variable)
        """
        super().__init__(metadata)
        self.api_key = api_key
        self.client = None
        
    async def load(self) -> bool:
        """
        Load the OpenAI model client.
        
        Returns:
            bool: True if loading was successful, False otherwise
        """
        try:
            # Try to import openai
            try:
                import openai
            except ImportError:
                self.logger.error("openai package not installed. Please install with: pip install openai")
                return False
            
            # Initialize client
            api_key = self.api_key or openai.api_key
            if not api_key:
                self.logger.error("OpenAI API key not provided")
                return False
                
            self.client = openai.AsyncOpenAI(api_key=api_key)
            self.logger.info(f"OpenAI model client initialized for {self.metadata.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to load OpenAI model {self.metadata.name}: {e}")
            return False
    
    async def unload(self) -> bool:
        """
        Unload the OpenAI model client.
        
        Returns:
            bool: True if unloading was successful, False otherwise
        """
        try:
            if self.client:
                # Close any open connections
                await self.client.close()
                self.client = None
            self.logger.info(f"OpenAI model {self.metadata.name} unloaded")
            return True
        except Exception as e:
            self.logger.error(f"Failed to unload OpenAI model {self.metadata.name}: {e}")
            return False
    
    async def generate(self, prompt: Union[str, Dict[str, Any]], **kwargs) -> ModelResponse:
        """
        Generate a response from the OpenAI model.
        
        Args:
            prompt: The input prompt
            **kwargs: Additional arguments for generation
            
        Returns:
            ModelResponse: The model's response
        """
        start_time = datetime.now()
        
        try:
            if not self.client:
                raise RuntimeError("Model not loaded")
            
            # Prepare messages
            if isinstance(prompt, str):
                messages = [{"role": "user", "content": prompt}]
            elif isinstance(prompt, dict) and "messages" in prompt:
                messages = prompt["messages"]
            else:
                messages = [{"role": "user", "content": str(prompt)}]
            
            # Prepare parameters
            params = {
                "model": self.metadata.config.get("model_name", "gpt-3.5-turbo"),
                "messages": messages,
                "temperature": kwargs.get('temperature', 0.7),
                "max_tokens": kwargs.get('max_tokens', 150),
                "top_p": kwargs.get('top_p', 1.0),
            }
            
            # Add any additional parameters from config
            for key, value in self.metadata.config.items():
                if key not in ['model_name'] and key not in params:
                    params[key] = value
            
            # Generate response
            response = await self.client.chat.completions.create(**params)
            
            end_time = datetime.now()
            latency_ms = (end_time - start_time).total_seconds() * 1000
            
            return ModelResponse(
                content=response.choices[0].message.content,
                metadata={
                    "model_type": "openai",
                    "model": params["model"],
                    "usage": response.usage.dict() if response.usage else {},
                    "finish_reason": response.choices[0].finish_reason,
                },
                model_name=self.metadata.name,
                latency_ms=latency_ms
            )
        except Exception as e:
            self.logger.error(f"Error generating response from OpenAI model: {e}")
            end_time = datetime.now()
            latency_ms = (end_time - start_time).total_seconds() * 1000
            
            return ModelResponse(
                content=f"Error generating response: {str(e)}",
                metadata={"error": True, "exception": str(e)},
                model_name=self.metadata.name,
                latency_ms=latency_ms
            )
    
    def get_info(self) -> Dict[str, Any]:
        """
        Get information about the OpenAI model.
        
        Returns:
            Dict[str, Any]: Dictionary containing model information
        """
        return {
            "name": self.metadata.name,
            "version": self.metadata.version,
            "type": self.metadata.model_type.value,
            "status": self.status.value,
            "description": self.metadata.description,
            "model_name": self.metadata.config.get("model_name", "unknown"),
            "capabilities": ["text_generation", "conversation", "json_mode"],
            "provider": "OpenAI"
        }


# Factory function to create language models
def create_language_model(model_type: str, metadata: ModelMetadata, **kwargs) -> BaseModel:
    """
    Factory function to create language models.
    
    Args:
        model_type: Type of model to create ("mock", "openai", "nano-gpt", etc.)
        metadata: Metadata for the model
        **kwargs: Additional arguments for model creation
        
    Returns:
        BaseModel: The created language model
    """
    if model_type.lower() == "mock":
        return MockLanguageModel(metadata)
    elif model_type.lower() == "openai":
        return OpenAILanguageModel(metadata, **kwargs)
    elif model_type.lower() in ["nano-gpt", "nanogpt"]:
        # Import here to avoid circular imports
        from .nano_gpt import NanoGPTLanguageModel
        return NanoGPTLanguageModel(metadata, **kwargs)
    else:
        raise ValueError(f"Unsupported language model type: {model_type}")
