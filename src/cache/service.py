"""
Cache Service for 4S1T Agent AI system.
Provides high-level caching operations.
"""
import logging
import json
from typing import Any, Optional, Union
from datetime import timedelta

import redis

from cache.connection import get_cache_connection, CacheConnection
from utils.logger import setup_logger

logger = setup_logger(__name__)


class CacheService:
    """Service for managing cache operations."""
    
    def __init__(self):
        """Initialize cache service."""
        self.cache_connection: CacheConnection = get_cache_connection()
        logger.info("Cache service initialized")
    
    def set(self, key: str, value: Any, expire: Optional[Union[int, timedelta]] = None) -> bool:
        """
        Set a value in the cache.
        
        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            expire: Expiration time in seconds or timedelta
            
        Returns:
            True if successful, False otherwise
        """
        try:
            client = self.cache_connection.get_client()
            
            # Serialize value to JSON string
            if isinstance(value, (dict, list, tuple, str, int, float, bool)) or value is None:
                serialized_value = json.dumps(value)
            else:
                serialized_value = str(value)
            
            # Set expiration
            if isinstance(expire, timedelta):
                expire_seconds = int(expire.total_seconds())
            else:
                expire_seconds = expire
            
            result = client.set(key, serialized_value, ex=expire_seconds)
            if result:
                logger.debug(f"Set cache key '{key}' with expiration {expire_seconds}s")
            else:
                logger.warning(f"Failed to set cache key '{key}'")
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to set cache key '{key}': {e}")
            return False
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from the cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found
        """
        try:
            client = self.cache_connection.get_client()
            value = client.get(key)
            
            if value is None:
                logger.debug(f"Cache miss for key '{key}'")
                return None
            
            # Try to deserialize JSON, fallback to string
            try:
                deserialized_value = json.loads(value)
                logger.debug(f"Cache hit for key '{key}'")
                return deserialized_value
            except json.JSONDecodeError:
                logger.debug(f"Cache hit for key '{key}' (string value)")
                return value
        except Exception as e:
            logger.error(f"Failed to get cache key '{key}': {e}")
            return None
    
    def delete(self, key: str) -> bool:
        """
        Delete a value from the cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if successful, False otherwise
        """
        try:
            client = self.cache_connection.get_client()
            result = client.delete(key)
            if result:
                logger.debug(f"Deleted cache key '{key}'")
            else:
                logger.debug(f"Cache key '{key}' not found for deletion")
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to delete cache key '{key}': {e}")
            return False
    
    def exists(self, key: str) -> bool:
        """
        Check if a key exists in the cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if key exists, False otherwise
        """
        try:
            client = self.cache_connection.get_client()
            result = client.exists(key)
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to check existence of cache key '{key}': {e}")
            return False
    
    def flush(self) -> bool:
        """
        Flush all cache entries.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            client = self.cache_connection.get_client()
            result = client.flushdb()
            if result:
                logger.info("Cache flushed successfully")
            else:
                logger.warning("Cache flush may have failed")
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to flush cache: {e}")
            return False
    
    def info(self) -> dict:
        """
        Get cache information.
        
        Returns:
            Dictionary with cache information
        """
        try:
            client = self.cache_connection.get_client()
            info = client.info()
            return {
                "connected_clients": info.get("connected_clients", 0),
                "used_memory": info.get("used_memory_human", "0B"),
                "total_commands_processed": info.get("total_commands_processed", 0),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0)
            }
        except Exception as e:
            logger.error(f"Failed to get cache info: {e}")
            return {}


# Global cache service instance
cache_service: Optional[CacheService] = None


def get_cache_service() -> CacheService:
    """
    Get singleton cache service instance.
    
    Returns:
        CacheService instance
    """
    global cache_service
    if cache_service is None:
        cache_service = CacheService()
    return cache_service
