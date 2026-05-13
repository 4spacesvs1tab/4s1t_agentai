"""
ChromaDB connection manager for 4S1T Agent AI system.
Handles connections to ChromaDB vector database.
"""
import logging
from typing import Optional, Dict, Any
import os

try:
    import chromadb
    from chromadb.config import Settings
    _CHROMADB_AVAILABLE = True
except ImportError:
    chromadb = None  # type: ignore[assignment]
    _CHROMADB_AVAILABLE = False

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger(__name__)


class VectorDatabaseConnection:
    """Manages ChromaDB vector database connections for the 4S1T Agent AI system."""
    
    def __init__(self):
        """
        Initialize vector database connection manager.
        """
        if not _CHROMADB_AVAILABLE:
            logger.warning("chromadb is not installed — vector database features are disabled.")
            self.client = None
            return

        # Ensure chroma directory exists
        os.makedirs(settings.CHROMA_PERSIST_DIR, exist_ok=True)

        self.client = None
        logger.info(f"Vector database connection manager initialized with persist dir: {settings.CHROMA_PERSIST_DIR}")
    
    def connect(self):
        if not _CHROMADB_AVAILABLE:
            raise RuntimeError("chromadb is not installed. Install it to enable vector database features.")
        return self._connect()

    def _connect(self):
        """
        Establish connection to ChromaDB.
        
        Returns:
            ChromaDB client instance
        """
        try:
            # Create ChromaDB persistent client
            self.client = chromadb.PersistentClient(
                path=settings.CHROMA_PERSIST_DIR
            )
            
            # Test connection
            self.client.heartbeat()
            logger.info(f"Connected to ChromaDB with persistent storage at {settings.CHROMA_PERSIST_DIR}")
            return self.client
        except Exception as e:
            logger.error(f"Failed to connect to ChromaDB: {e}")
            raise
    
    def disconnect(self):
        """Close ChromaDB connection."""
        if self.client:
            # ChromaDB persistent client doesn't require explicit closing
            self.client = None
            logger.info("ChromaDB connection closed")
    
    def get_client(self) -> Any:
        """
        Get ChromaDB client instance.
        
        Returns:
            ChromaDB client instance
        """
        if not self.client:
            self.connect()
        return self.client
    
    def create_collection(self, name: str, metadata: Optional[Dict[str, Any]] = None) -> Any:
        """
        Create a new collection in ChromaDB.
        
        Args:
            name: Name of the collection
            metadata: Optional metadata for the collection
            
        Returns:
            ChromaDB collection instance
        """
        try:
            client = self.get_client()
            collection = client.create_collection(name=name, metadata=metadata)
            logger.info(f"Created collection '{name}' in ChromaDB")
            return collection
        except Exception as e:
            logger.error(f"Failed to create collection '{name}': {e}")
            raise
    
    def get_collection(self, name: str) -> Any:
        """
        Get an existing collection from ChromaDB.
        
        Args:
            name: Name of the collection
            
        Returns:
            ChromaDB collection instance
        """
        try:
            client = self.get_client()
            collection = client.get_collection(name=name)
            logger.debug(f"Retrieved collection '{name}' from ChromaDB")
            return collection
        except Exception as e:
            logger.error(f"Failed to get collection '{name}': {e}")
            raise
    
    def delete_collection(self, name: str):
        """
        Delete a collection from ChromaDB.
        
        Args:
            name: Name of the collection to delete
        """
        try:
            client = self.get_client()
            client.delete_collection(name=name)
            logger.info(f"Deleted collection '{name}' from ChromaDB")
        except Exception as e:
            logger.error(f"Failed to delete collection '{name}': {e}")
            raise


# Global vector database connection instance
vector_db_connection: Optional[VectorDatabaseConnection] = None


def get_vector_database_connection() -> VectorDatabaseConnection:
    """
    Get singleton vector database connection instance.
    
    Returns:
        VectorDatabaseConnection instance
    """
    global vector_db_connection
    if vector_db_connection is None:
        vector_db_connection = VectorDatabaseConnection()
    return vector_db_connection
