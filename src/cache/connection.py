"""
Redis connection manager for 4S1T Agent AI system.
Handles connections to Redis cache.
"""
import logging
from typing import Optional, Any

import redis

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger(__name__)


class CacheConnection:
    """Manages Redis cache connections for the 4S1T Agent AI system."""
    
    def __init__(self, host: Optional[str] = None, port: Optional[int] = None,
                 db: Optional[int] = None, password: Optional[str] = None):
        """
        Initialize cache connection manager.
        
        Args:
            host: Redis host (defaults to settings.REDIS_URL parsed host)
            port: Redis port (defaults to settings.REDIS_URL parsed port)
            db: Redis database number (defaults to 0)
            password: Redis password (if required)
        """
        # Parse Redis URL to extract host and port
        redis_url = settings.REDIS_URL
        if redis_url.startswith("redis://"):
            # Remove "redis://" prefix
            url_parts = redis_url[8:].split("/")
            host_port = url_parts[0].split("@")[-1]  # Handle password in URL
            if ":" in host_port:
                default_host, port_str = host_port.split(":")
                default_port = int(port_str)
            else:
                default_host = host_port
                default_port = 6379
        else:
            default_host = "localhost"
            default_port = 6379
            
        self.host = host or default_host
        self.port = port or default_port
        self.db = db or 0
        self.password = password
        self.client: Optional[redis.Redis] = None
        logger.info(f"Cache connection manager initialized with host: {self.host}, port: {self.port}, db: {self.db}")
    
    def connect(self) -> redis.Redis:
        """
        Establish connection to Redis.
        
        Returns:
            Redis client instance
        """
        try:
            # Create Redis client
            self.client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            
            # Test connection
            self.client.ping()
            logger.info(f"Connected to Redis at {self.host}:{self.port}")
            return self.client
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def disconnect(self):
        """Close Redis connection."""
        if self.client:
            try:
                self.client.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis connection: {e}")
            finally:
                self.client = None
    
    def get_client(self) -> redis.Redis:
        """
        Get Redis client instance.
        
        Returns:
            Redis client instance
        """
        if not self.client:
            self.connect()
        return self.client


# Global cache connection instance
cache_connection: Optional[CacheConnection] = None


def get_cache_connection() -> CacheConnection:
    """
    Get singleton cache connection instance.
    
    Returns:
        CacheConnection instance
    """
    global cache_connection
    if cache_connection is None:
        cache_connection = CacheConnection()
    return cache_connection
