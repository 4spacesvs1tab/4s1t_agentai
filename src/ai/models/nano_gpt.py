"""
Nano-GPT.com language model integration for the 4S1T Agent AI framework.

This module provides integration with nano-gpt.com API, specifically designed
to leverage the PRO subscription benefits with access to all open-source models.
"""

import asyncio
import json
import logging
import aiohttp
from typing import Any, Dict, List, Optional, Union, AsyncGenerator
from datetime import datetime
import time

from .base import BaseModel, ModelMetadata, ModelResponse, ModelStatus, ModelType


logger = logging.getLogger(__name__)


class NanoGPTLanguageModel(BaseModel):
    """
    Nano-GPT.com language model integration.
    
    This model integrates with nano-gpt.com's API for language model capabilities,
    optimized for PRO subscription users with access to all open-source models.
    """
    
    def __init__(self, metadata: ModelMetadata, api_key: Optional[str] = None):
        """
        Initialize the Nano-GPT language model.
        
        Args:
            metadata: Metadata describing the model
            api_key: Nano-GPT API key (can also be set via environment variable)
        """
        super().__init__(metadata)
        self.api_key = api_key
        # Construct full API endpoint from base URL and endpoint
        base_url = metadata.config.get("base_url", "https://nano-gpt.com/api")
        api_endpoint = metadata.config.get("api_endpoint", "/v1/chat/completions")
        self.api_endpoint = f"{base_url.rstrip('/')}{api_endpoint}"
        self.session = None
        self.subscription_tier = metadata.config.get("subscription_tier", "FREE")
        self.rate_limit_remaining = None
        self.rate_limit_reset = None
        
        # PRO subscription model catalog
        self.pro_models = {
            # Reasoning Models
            "glm-4.6": {"category": "reasoning", "context_window": 128000, "description": "Advanced reasoning and chat"},
            "glm-4.5": {"category": "reasoning", "context_window": 128000, "description": "Advanced reasoning"},
            "deepseek-r1": {"category": "reasoning", "context_window": 128000, "description": "Thinking, analysis"},
            
            # General Purpose Models
            "deepseek-v3.2": {"category": "general", "context_window": 128000, "description": "Balanced performance"},
            "deepseek-v3.1": {"category": "general", "context_window": 128000, "description": "Balanced performance"},
            
            # Fast Response Models
            "kimi-k2-0905": {"category": "fast_response", "context_window": 128000, "description": "Quick responses"},
            "kimi-k2-0711": {"category": "fast_response", "context_window": 128000, "description": "Quick responses"},
            
            # Coding Models
            "qwen3-coder": {"category": "coding", "context_window": 128000, "description": "Programming assistance"},
            "coding-specialists": {"category": "coding", "context_window": 128000, "description": "Programming assistance"},
            
            # Math Models
            "math-models": {"category": "math", "context_window": 128000, "description": "Calculations"},
            
            # Specialty Models
            "venice": {"category": "uncensored", "context_window": 128000, "description": "Special use cases"},
            "roleplaying": {"category": "roleplaying", "context_window": 128000, "description": "Special finetunes"}
        }
    
    async def load(self) -> bool:
        """
        Load the Nano-GPT model client.
        
        Returns:
            bool: True if loading was successful, False otherwise
        """
        try:
            # Initialize aiohttp session with optimized connection pooling
            if self.session is None or self.session.closed:
                # Increased timeout for complex prompts and reasoning models
                # Reasoning models (Kimi K2.5 Thinking, etc.) can take 3-4 minutes
                timeout = aiohttp.ClientTimeout(total=300, connect=10, sock_read=240)
                
                # Optimized TCP connector for better performance
                connector = aiohttp.TCPConnector(
                    limit=20,              # Max simultaneous connections
                    limit_per_host=10,     # Max connections per host
                    enable_cleanup_closed=True,
                    force_close=False,     # Keep connections alive (HTTP keep-alive)
                    ttl_dns_cache=300,     # DNS cache for 5 minutes
                    use_dns_cache=True,
                )
                
                self.session = aiohttp.ClientSession(
                    timeout=timeout,
                    connector=connector,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    }
                )
            
            self.logger.info(f"Nano-GPT model client initialized for {self.metadata.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to load Nano-GPT model {self.metadata.name}: {e}")
            return False
    
    async def unload(self) -> bool:
        """
        Unload the Nano-GPT model client.
        
        Returns:
            bool: True if unloading was successful, False otherwise
        """
        try:
            if self.session and not self.session.closed:
                await self.session.close()
                self.session = None
            self.logger.info(f"Nano-GPT model {self.metadata.name} unloaded")
            return True
        except Exception as e:
            self.logger.error(f"Failed to unload Nano-GPT model {self.metadata.name}: {e}")
            return False
    
    async def generate(self, prompt: Union[str, Dict[str, Any]], **kwargs) -> ModelResponse:
        """
        Generate a response from the Nano-GPT model.
        
        Args:
            prompt: The input prompt
            **kwargs: Additional arguments for generation
            
        Returns:
            ModelResponse: The model's response
        """
        start_time = datetime.now()
        
        try:
            if not self.session:
                raise RuntimeError("Model not loaded")
            
            # Check rate limits
            if not await self._check_rate_limit():
                # Wait until rate limit resets
                if self.rate_limit_reset:
                    wait_time = max(0, self.rate_limit_reset - time.time())
                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
            
            # Prepare messages
            if isinstance(prompt, str):
                messages = [{"role": "user", "content": prompt}]
            elif isinstance(prompt, dict) and "messages" in prompt:
                messages = prompt["messages"]
            else:
                messages = [{"role": "user", "content": str(prompt)}]
            
            # Prepare parameters
            model_name = kwargs.get('model', self.metadata.config.get("default_model", "glm-4.6"))
            
            params = {
                "model": model_name,
                "messages": messages,
                "temperature": kwargs.get('temperature', 0.7),
                "max_tokens": kwargs.get('max_tokens', 150),
                "top_p": kwargs.get('top_p', 1.0),
            }
            
            # Add streaming parameter if requested
            if kwargs.get('stream', False):
                params['stream'] = True
                return await self._generate_stream(params, start_time)
            
            # Add any additional parameters from config
            for key, value in self.metadata.config.items():
                if key not in ['api_endpoint', 'default_model'] and key not in params:
                    params[key] = value
            
            # Make API call with retry logic
            response_data = await self._make_api_call_with_retry(params)
            
            end_time = datetime.now()
            latency_ms = (end_time - start_time).total_seconds() * 1000
            
            # Extract comprehensive metadata including pricing information
            metadata = {
                "model_type": "nano-gpt",
                "model": params["model"],
                "usage": response_data.get("usage", {}),
                "finish_reason": response_data["choices"][0]["finish_reason"],
                "provider": "nano-gpt.com",
                "subscription_tier": self.subscription_tier,
                "pricing": response_data.get("x_nanogpt_pricing", {})
            }
            
            # Add reasoning information if available
            reasoning_info = response_data.get("reasoning") or response_data.get("reasoning_details")
            if reasoning_info:
                metadata["reasoning"] = reasoning_info
            
            return ModelResponse(
                content=response_data["choices"][0]["message"]["content"],
                metadata=metadata,
                model_name=self.metadata.name,
                latency_ms=latency_ms
            )
        except Exception as e:
            self.logger.error(f"Error generating response from Nano-GPT model: {e}")
            end_time = datetime.now()
            latency_ms = (end_time - start_time).total_seconds() * 1000
            
            return ModelResponse(
                content=f"Error generating response: {str(e)}",
                metadata={"error": True, "exception": str(e)},
                model_name=self.metadata.name,
                latency_ms=latency_ms
            )
    
    async def _generate_stream(self, params: Dict[str, Any], start_time: datetime) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generate a streaming response from the Nano-GPT model.
        
        Args:
            params: API parameters
            start_time: Start time for latency calculation
            
        Returns:
            AsyncGenerator: Stream of response chunks
        """
        try:
            if not self.session:
                raise RuntimeError("Model not loaded")
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            async with self.session.post(self.api_endpoint, json=params, headers=headers) as response:
                # Update rate limit info
                self._update_rate_limit_info(response)
                
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API error {response.status}: {error_text}")
                
                # Process streaming response
                accumulated_content = ""
                accumulated_reasoning = ""
                pricing_info = {}
                
                async for line in response.content:
                    if line:
                        decoded_line = line.decode('utf-8').strip()
                        if decoded_line.startswith('data: '):
                            data = decoded_line[6:]  # Remove 'data: ' prefix
                            if data == '[DONE]':
                                # Send final chunk with accumulated data
                                end_time = datetime.now()
                                latency_ms = (end_time - start_time).total_seconds() * 1000
                                
                                yield {
                                    "content": accumulated_content,
                                    "reasoning": accumulated_reasoning,
                                    "pricing": pricing_info,
                                    "done": True,
                                    "latency_ms": latency_ms
                                }
                                break
                            else:
                                try:
                                    chunk_data = json.loads(data)
                                    
                                    # Check for pricing information
                                    if 'x_nanogpt_pricing' in chunk_data:
                                        pricing_info = chunk_data['x_nanogpt_pricing']
                                    
                                    # Extract content and reasoning
                                    if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                                        choice = chunk_data['choices'][0]
                                        if 'delta' in choice:
                                            delta = choice['delta']
                                            
                                            # Accumulate content
                                            if 'content' in delta and delta['content']:
                                                accumulated_content += delta['content']
                                            
                                            # Accumulate reasoning
                                            if 'reasoning' in delta and delta['reasoning']:
                                                accumulated_reasoning += delta['reasoning']
                                    
                                    # Yield the chunk data
                                    yield {
                                        "chunk": chunk_data,
                                        "content_delta": chunk_data.get('choices', [{}])[0].get('delta', {}).get('content', ''),
                                        "reasoning_delta": chunk_data.get('choices', [{}])[0].get('delta', {}).get('reasoning', ''),
                                        "pricing": pricing_info,
                                        "done": False
                                    }
                                except json.JSONDecodeError:
                                    self.logger.warning(f"Failed to parse streaming chunk: {data}")
        except Exception as e:
            self.logger.error(f"Error in streaming response from Nano-GPT model: {e}")
            raise
    
    async def _make_api_call_with_retry(self, params: Dict[str, Any], max_retries: int = 3) -> Dict[str, Any]:
        """
        Make API call with retry logic and exponential backoff.
        
        Args:
            params: API parameters
            max_retries: Maximum number of retry attempts
            
        Returns:
            Dict[str, Any]: API response data
            
        Raises:
            Exception: If all retry attempts fail
        """
        for attempt in range(max_retries + 1):
            try:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                async with self.session.post(self.api_endpoint, json=params, headers=headers) as response:
                    # Update rate limit info
                    self._update_rate_limit_info(response)
                    
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 429:  # Rate limited
                        if attempt < max_retries:
                            # Exponential backoff
                            wait_time = (2 ** attempt) + (0.1 * attempt)
                            self.logger.warning(f"Rate limited, waiting {wait_time}s before retry {attempt + 1}")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise Exception("Rate limit exceeded, max retries reached")
                    elif response.status >= 500:  # Server error
                        if attempt < max_retries:
                            # Exponential backoff for server errors
                            wait_time = (2 ** attempt) + (0.1 * attempt)
                            self.logger.warning(f"Server error {response.status}, waiting {wait_time}s before retry {attempt + 1}")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise Exception(f"Server error {response.status}, max retries reached")
                    else:
                        # Client error or other status
                        error_text = await response.text()
                        # Try to parse as JSON for OpenAI-style error envelope
                        try:
                            error_data = json.loads(error_text)
                            if "error" in error_data:
                                error_obj = error_data["error"]
                                message = error_obj.get("message", "Unknown error")
                                error_type = error_obj.get("type", "api_error")
                                code = error_obj.get("code", response.status)
                                param = error_obj.get("param", None)
                                
                                # Include request ID if available
                                request_id = response.headers.get('x-request-id')
                                if request_id:
                                    message += f" (Request ID: {request_id})"
                                
                                raise Exception(f"Nano-GPT API Error [{code}]: {message} (type: {error_type})")
                            else:
                                raise Exception(f"API error {response.status}: {error_text}")
                        except json.JSONDecodeError:
                            raise Exception(f"API error {response.status}: {error_text}")
                        
            except Exception as e:
                if attempt < max_retries:
                    # Exponential backoff for network errors
                    wait_time = (2 ** attempt) + (0.1 * attempt)
                    self.logger.warning(f"Network error: {e}, waiting {wait_time}s before retry {attempt + 1}")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    raise Exception(f"Network error after {max_retries + 1} attempts: {e}")
        
        raise Exception("Max retries exceeded")
    
    def _update_rate_limit_info(self, response):
        """
        Update rate limit information from response headers.
        
        Args:
            response: aiohttp response object
        """
        try:
            # Extract rate limit headers based on Nano-GPT documentation
            # Note: Actual header names may vary, implementing what's commonly used
            remaining = (response.headers.get('X-RateLimit-Remaining') or 
                        response.headers.get('RateLimit-Remaining') or
                        response.headers.get('X-Ratelimit-Remaining'))
            reset = (response.headers.get('X-RateLimit-Reset') or 
                    response.headers.get('RateLimit-Reset') or
                    response.headers.get('X-Ratelimit-Reset'))
            
            # Also check for request ID for better error tracking
            request_id = response.headers.get('x-request-id')
            if request_id:
                self.logger.debug(f"Request ID: {request_id}")
            
            if remaining is not None:
                self.rate_limit_remaining = int(remaining)
            if reset is not None:
                self.rate_limit_reset = int(reset)
                
        except Exception as e:
            self.logger.debug(f"Could not parse rate limit headers: {e}")
    
    async def _check_rate_limit(self) -> bool:
        """
        Check if we're within rate limits.
        
        Returns:
            bool: True if we can make requests, False if rate limited
        """
        if self.rate_limit_remaining is not None and self.rate_limit_remaining <= 0:
            if self.rate_limit_reset and time.time() < self.rate_limit_reset:
                return False
        return True
    
    def get_info(self) -> Dict[str, Any]:
        """
        Get information about the Nano-GPT model.
        
        Returns:
            Dict[str, Any]: Dictionary containing model information
        """
        return {
            "name": self.metadata.name,
            "version": self.metadata.version,
            "type": self.metadata.model_type.value,
            "status": self.status.value,
            "description": self.metadata.description,
            "model_name": self.metadata.config.get("default_model", "unknown"),
            "capabilities": ["text_generation", "conversation", "json_mode"],
            "provider": "nano-gpt.com",
            "subscription_tier": self.subscription_tier,
            "pro_models_available": len(self.pro_models) if self.subscription_tier == "PRO" else 0
        }
    
    def get_model_catalog(self) -> Dict[str, Any]:
        """
        Get the model catalog for PRO subscription.
        
        Returns:
            Dict[str, Any]: Model catalog information
        """
        return {
            "subscription_tier": self.subscription_tier,
            "models": self.pro_models if self.subscription_tier == "PRO" else {},
            "total_models": len(self.pro_models) if self.subscription_tier == "PRO" else 0
        }
    
    def is_model_available(self, model_name: str) -> bool:
        """
        Check if a model is available in the PRO subscription.
        
        Args:
            model_name: Name of the model to check
            
        Returns:
            bool: True if model is available, False otherwise
        """
        if self.subscription_tier != "PRO":
            return False
        return model_name in self.pro_models


# Factory function to create Nano-GPT language models
def create_nano_gpt_model(metadata: ModelMetadata, **kwargs) -> BaseModel:
    """
    Factory function to create Nano-GPT language models.
    
    Args:
        metadata: Metadata for the model
        **kwargs: Additional arguments for model creation
        
    Returns:
        BaseModel: The created Nano-GPT language model
    """
    return NanoGPTLanguageModel(metadata, **kwargs)
