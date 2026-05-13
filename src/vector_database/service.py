"""
Vector Database Service for 4S1T Agent AI system.
Provides high-level operations for vector database management.
"""
import logging
from typing import List, Dict, Any, Optional

try:
    import chromadb  # noqa: F401
    _CHROMADB_AVAILABLE = True
except ImportError:
    _CHROMADB_AVAILABLE = False

from vector_database.connection import get_vector_database_connection, VectorDatabaseConnection
from utils.logger import setup_logger

logger = setup_logger(__name__)


class VectorDatabaseService:
    """Service for managing vector database operations."""
    
    def __init__(self):
        """Initialize vector database service."""
        self.db_connection: VectorDatabaseConnection = get_vector_database_connection()
        logger.info("Vector database service initialized")
    
    def initialize_collections(self):
        """Initialize required collections based on database design."""
        if not _CHROMADB_AVAILABLE:
            logger.warning("chromadb not installed — skipping vector collection initialisation.")
            return
        try:
            # Initialize babok_knowledge collection
            try:
                self.db_connection.get_collection("babok_knowledge")
                logger.info("Collection 'babok_knowledge' already exists")
            except Exception:
                # Collection doesn't exist, create it
                self.db_connection.create_collection(
                    name="babok_knowledge",
                    metadata={
                        "description": "IIBA BABOK content for business analysis guidance",
                        "chapter_field": "chapter",
                        "section_field": "section",
                        "keywords_field": "keywords"
                    }
                )
                logger.info("Created collection 'babok_knowledge'")
            
            # Initialize user_interactions collection
            try:
                self.db_connection.get_collection("user_interactions")
                logger.info("Collection 'user_interactions' already exists")
            except Exception:
                # Collection doesn't exist, create it
                self.db_connection.create_collection(
                    name="user_interactions",
                    metadata={
                        "description": "Historical record of user interactions for learning and personalization",
                        "user_id_field": "user_id",
                        "feedback_field": "feedback"
                    }
                )
                logger.info("Created collection 'user_interactions'")
            
            # Initialize best_practices collection
            try:
                self.db_connection.get_collection("best_practices")
                logger.info("Collection 'best_practices' already exists")
            except Exception:
                # Collection doesn't exist, create it
                self.db_connection.create_collection(
                    name="best_practices",
                    metadata={
                        "description": "Data analysis best practices and methodologies",
                        "category_field": "category",
                        "implementation_field": "implementation"
                    }
                )
                logger.info("Created collection 'best_practices'")
                
        except Exception as e:
            logger.error(f"Failed to initialize collections: {e}")
            raise
    
    def add_documents(self, collection_name: str, documents: List[str], 
                     metadatas: Optional[List[Dict[str, Any]]] = None,
                     ids: Optional[List[str]] = None) -> List[str]:
        """
        Add documents to a collection.
        
        Args:
            collection_name: Name of the collection
            documents: List of document texts
            metadatas: Optional list of metadata dictionaries
            ids: Optional list of document IDs
            
        Returns:
            List of generated or provided document IDs
        """
        try:
            collection = self.db_connection.get_collection(collection_name)
            
            # Generate IDs if not provided
            if ids is None:
                import uuid
                ids = [str(uuid.uuid4()) for _ in range(len(documents))]
            
            collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            logger.info(f"Added {len(documents)} documents to collection '{collection_name}'")
            return ids
        except Exception as e:
            logger.error(f"Failed to add documents to collection '{collection_name}': {e}")
            raise
    
    def query_collection(self, collection_name: str, query_texts: List[str],
                        n_results: int = 5, where: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Query a collection with text search.
        
        Args:
            collection_name: Name of the collection
            query_texts: List of query texts
            n_results: Number of results to return
            where: Optional filter conditions
            
        Returns:
            Query results
        """
        try:
            collection = self.db_connection.get_collection(collection_name)
            results = collection.query(
                query_texts=query_texts,
                n_results=n_results,
                where=where
            )
            logger.debug(f"Query returned {len(results['ids'][0])} results from collection '{collection_name}'")
            return results
        except Exception as e:
            logger.error(f"Failed to query collection '{collection_name}': {e}")
            raise
    
    def get_document_count(self, collection_name: str) -> int:
        """
        Get the number of documents in a collection.
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            Number of documents in the collection
        """
        try:
            collection = self.db_connection.get_collection(collection_name)
            count = collection.count()
            logger.debug(f"Collection '{collection_name}' contains {count} documents")
            return count
        except Exception as e:
            logger.error(f"Failed to get document count for collection '{collection_name}': {e}")
            raise
    
    def delete_documents(self, collection_name: str, ids: List[str]):
        """
        Delete documents from a collection.
        
        Args:
            collection_name: Name of the collection
            ids: List of document IDs to delete
        """
        try:
            collection = self.db_connection.get_collection(collection_name)
            collection.delete(ids=ids)
            logger.info(f"Deleted {len(ids)} documents from collection '{collection_name}'")
        except Exception as e:
            logger.error(f"Failed to delete documents from collection '{collection_name}': {e}")
            raise


# Global vector database service instance
vector_db_service: Optional[VectorDatabaseService] = None


def get_vector_database_service() -> VectorDatabaseService:
    """
    Get singleton vector database service instance.
    
    Returns:
        VectorDatabaseService instance
    """
    global vector_db_service
    if vector_db_service is None:
        vector_db_service = VectorDatabaseService()
    return vector_db_service
