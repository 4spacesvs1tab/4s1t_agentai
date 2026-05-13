"""
Additional Nano-GPT API endpoints for the 4S1T Agent AI framework.

This module provides integration with nano-gpt.com's additional API endpoints
including embeddings, audio speech, and models listing.
"""

import asyncio
import json
import aiohttp
from typing import Any, Dict, List, Optional, Union, AsyncGenerator
from datetime import datetime
import time

from utils.logger import setup_logger
logger = setup_logger(__name__)


class NanoGPTApiClient:
    """
    Client for additional Nano-GPT API endpoints.
    """
    
    def __init__(self, api_key: str, base_url: str = "https://nano-gpt.com/api"):
        """
        Initialize the Nano-GPT API client.
        
        Args:
            api_key: Nano-GPT API key
            base_url: Base URL for the API (default: https://nano-gpt.com/api)
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.session = None
    
    async def initialize(self) -> bool:
        """
        Initialize the HTTP session.
        
        Returns:
            bool: True if initialization was successful, False otherwise
        """
        try:
            if self.session is None or self.session.closed:
                timeout = aiohttp.ClientTimeout(total=30)
                self.session = aiohttp.ClientSession(timeout=timeout)
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Nano-GPT API client: {e}")
            return False
    
    async def close(self) -> bool:
        """
        Close the HTTP session.
        
        Returns:
            bool: True if closing was successful, False otherwise
        """
        try:
            if self.session and not self.session.closed:
                await self.session.close()
                self.session = None
            return True
        except Exception as e:
            logger.error(f"Failed to close Nano-GPT API client: {e}")
            return False
    
    async def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, 
                          params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Make an API request with proper error handling.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            data: Request data for POST/PUT requests
            params: Query parameters for GET requests
            
        Returns:
            Dict[str, Any]: API response data
            
        Raises:
            Exception: If the request fails
        """
        if not self.session:
            await self.initialize()
        
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            if method.upper() == "GET":
                async with self.session.get(url, headers=headers, params=params) as response:
                    return await self._handle_response(response)
            elif method.upper() == "POST":
                async with self.session.post(url, headers=headers, json=data) as response:
                    return await self._handle_response(response)
            else:
                raise Exception(f"Unsupported HTTP method: {method}")
        except Exception as e:
            logger.error(f"API request failed: {e}")
            raise
    
    async def _handle_response(self, response) -> Dict[str, Any]:
        """
        Handle API response with proper error handling.
        
        Args:
            response: aiohttp response object
            
        Returns:
            Dict[str, Any]: Response data
            
        Raises:
            Exception: If the response indicates an error
        """
        if response.status == 200:
            return await response.json()
        else:
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
    
    async def list_models(self, detailed: bool = False) -> Dict[str, Any]:
        """
        List available models.
        
        Args:
            detailed: Whether to include detailed information
            
        Returns:
            Dict[str, Any]: Models information
        """
        params = {"detailed": "true" if detailed else "false"}
        return await self._make_request("GET", "/v1/models", params=params)
    
    async def create_embeddings(self, model: str, input: Union[str, List[str]], 
                              encoding_format: str = "float") -> Dict[str, Any]:
        """
        Create embeddings for text.
        
        Args:
            model: Model to use for embeddings
            input: Text input (single string or list of strings)
            encoding_format: Encoding format ("float" or "base64")
            
        Returns:
            Dict[str, Any]: Embeddings response
        """
        data = {
            "model": model,
            "input": input,
            "encoding_format": encoding_format
        }
        return await self._make_request("POST", "/v1/embeddings", data=data)
    
    async def create_audio_speech(self, model: str, input: str, voice: str, 
                                response_format: str = "mp3", speed: float = 1.0) -> bytes:
        """
        Create audio speech from text.
        
        Args:
            model: Model to use for speech synthesis
            input: Text input
            voice: Voice to use
            response_format: Audio format ("mp3", "opus", "aac", "flac")
            speed: Speed of speech (0.25 to 4.0)
            
        Returns:
            bytes: Audio data
        """
        if not self.session:
            await self.initialize()
        
        url = f"{self.base_url}/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "input": input,
            "voice": voice,
            "response_format": response_format,
            "speed": speed
        }
        
        try:
            async with self.session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    return await response.read()
                else:
                    error_text = await response.text()
                    # Try to parse as JSON for OpenAI-style error envelope
                    try:
                        error_data = json.loads(error_text)
                        if "error" in error_data:
                            error_obj = error_data["error"]
                            message = error_obj.get("message", "Unknown error")
                            error_type = error_obj.get("type", "api_error")
                            code = error_obj.get("code", response.status)
                            
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
            logger.error(f"Audio speech API request failed: {e}")
            raise
    
    async def manage_provider_keys(self, provider: str, key: str) -> Dict[str, Any]:
        """
        Manage provider keys for BYOK (Bring Your Own Key) functionality.
        
        Args:
            provider: Provider name (e.g., "openai", "anthropic")
            key: Provider API key
            
        Returns:
            Dict[str, Any]: Response data
        """
        data = {
            "provider": provider,
            "key": key
        }
        return await self._make_request("POST", "/user/provider-keys", data=data)


# Convenience functions for common operations
async def get_available_models(api_key: str, detailed: bool = False) -> Dict[str, Any]:
    """
    Get list of available models.
    
    Args:
        api_key: Nano-GPT API key
        detailed: Whether to include detailed information
        
    Returns:
        Dict[str, Any]: Models information
    """
    client = NanoGPTApiClient(api_key)
    try:
        result = await client.list_models(detailed=detailed)
        return result
    finally:
        await client.close()


async def generate_embeddings(api_key: str, model: str, input: Union[str, List[str]], 
                            encoding_format: str = "float") -> Dict[str, Any]:
    """
    Generate embeddings for text.
    
    Args:
        api_key: Nano-GPT API key
        model: Model to use for embeddings
        input: Text input (single string or list of strings)
        encoding_format: Encoding format ("float" or "base64")
        
    Returns:
        Dict[str, Any]: Embeddings response
    """
    client = NanoGPTApiClient(api_key)
    try:
        result = await client.create_embeddings(model, input, encoding_format)
        return result
    finally:
        await client.close()


async def generate_audio_speech(api_key: str, model: str, input: str, voice: str, 
                              response_format: str = "mp3", speed: float = 1.0) -> bytes:
    """
    Generate audio speech from text.
    
    Args:
        api_key: Nano-GPT API key
        model: Model to use for speech synthesis
        input: Text input
        voice: Voice to use
        response_format: Audio format ("mp3", "opus", "aac", "flac")
        speed: Speed of speech (0.25 to 4.0)
        
    Returns:
        bytes: Audio data
    """
    client = NanoGPTApiClient(api_key)
    try:
        result = await client.create_audio_speech(model, input, voice, response_format, speed)
        return result
    finally:
        await client.close()
