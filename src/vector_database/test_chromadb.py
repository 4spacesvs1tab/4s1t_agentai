"""
Test script for ChromaDB integration.
"""
import sys
import os

# Add src to path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from vector_database.service import get_vector_database_service


def test_chromadb_integration():
    """Test ChromaDB integration functionality."""
    print("=== 4S1T Agent AI ChromaDB Integration Test ===\n")
    
    try:
        # Initialize service
        print("1. Initializing vector database service...")
        vector_db_service = get_vector_database_service()
        print("   ✓ Vector database service initialized successfully\n")
        
        # Initialize collections
        print("2. Initializing collections...")
        vector_db_service.initialize_collections()
        print("   ✓ Collections initialized successfully\n")
        
        # Test adding documents to babok_knowledge collection
        print("3. Testing document addition to babok_knowledge collection:")
        babok_docs = [
            "Business analysis is the practice of enabling change in an organizational context by defining needs and recommending solutions that deliver value to stakeholders.",
            "Requirements development is the process of discovering, analyzing, documenting, and managing stakeholder needs and requirements.",
            "Stakeholder engagement involves identifying, analyzing, planning for, and engaging with stakeholders throughout the business analysis process."
        ]
        
        babok_metadatas = [
            {"chapter": "1", "section": "1.2", "keywords": "business_analysis,change,value"},
            {"chapter": "2", "section": "2.3", "keywords": "requirements,development,stakeholders"},
            {"chapter": "3", "section": "3.1", "keywords": "stakeholder,engagement,planning"}
        ]
        
        babok_ids = vector_db_service.add_documents(
            collection_name="babok_knowledge",
            documents=babok_docs,
            metadatas=babok_metadatas
        )
        print(f"   ✓ Added {len(babok_docs)} documents to babok_knowledge collection")
        print(f"   Document IDs: {babok_ids}\n")
        
        # Test querying the collection
        print("4. Testing query functionality:")
        query_results = vector_db_service.query_collection(
            collection_name="babok_knowledge",
            query_texts=["What is business analysis?"],
            n_results=2
        )
        
        print(f"   ✓ Query returned {len(query_results['ids'][0])} results")
        print(f"   Top result: {query_results['documents'][0][0][:100]}...\n")
        
        # Test document count
        print("5. Testing document counting:")
        count = vector_db_service.get_document_count("babok_knowledge")
        print(f"   ✓ Collection contains {count} documents\n")
        
        # Test adding documents to best_practices collection
        print("6. Testing document addition to best_practices collection:")
        practices_docs = [
            "Always validate data inputs before processing to prevent errors and security vulnerabilities.",
            "Use version control for all analytical code and documentation to track changes and enable collaboration."
        ]
        
        practices_metadatas = [
            {"category": "data_validation", "implementation": "Input sanitization, type checking"},
            {"category": "version_control", "implementation": "Git with branching strategy"}
        ]
        
        practices_ids = vector_db_service.add_documents(
            collection_name="best_practices",
            documents=practices_docs,
            metadatas=practices_metadatas
        )
        print(f"   ✓ Added {len(practices_docs)} documents to best_practices collection")
        print(f"   Document IDs: {practices_ids}\n")
        
        print("=== ChromaDB integration test completed successfully ===")
        return True
        
    except Exception as e:
        print(f"=== ChromaDB integration test FAILED ===")
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    success = test_chromadb_integration()
    sys.exit(0 if success else 1)
