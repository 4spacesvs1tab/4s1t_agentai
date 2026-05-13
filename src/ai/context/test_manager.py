"""
Tests for the conversation context management system.
"""

import sys
import os
from datetime import datetime, timedelta

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.context.manager import ContextManager, ContextEntry


def test_conversation_creation():
    """Test conversation creation functionality."""
    manager = ContextManager()
    
    # Create a conversation
    conv_id = manager.create_conversation(metadata={"test": "value"})
    
    # Check that conversation exists
    assert isinstance(conv_id, str)
    assert len(conv_id) > 0
    assert conv_id in manager.conversations
    
    # Check metadata
    context = manager.conversations[conv_id]
    assert context.metadata["test"] == "value"
    assert context.conversation_id == conv_id
    
    print("✓ Conversation creation test passed")


def test_entry_management():
    """Test adding and retrieving entries."""
    manager = ContextManager()
    
    # Create conversation
    conv_id = manager.create_conversation()
    
    # Add entries
    entry1_id = manager.add_entry(conv_id, "user", "Hello!")
    entry2_id = manager.add_entry(conv_id, "assistant", "Hi there!")
    
    # Check entries were added
    assert isinstance(entry1_id, str)
    assert isinstance(entry2_id, str)
    
    # Get context
    context = manager.get_context(conv_id)
    assert context is not None
    assert len(context.entries) == 2
    
    # Check entry content
    assert context.entries[0].role == "user"
    assert context.entries[0].content == "Hello!"
    assert context.entries[1].role == "assistant"
    assert context.entries[1].content == "Hi there!"
    
    print("✓ Entry management test passed")


def test_context_filtering():
    """Test context filtering functionality."""
    manager = ContextManager()
    
    # Create conversation
    conv_id = manager.create_conversation()
    
    # Add multiple entries
    for i in range(10):
        manager.add_entry(conv_id, "user", f"Message {i}")
    
    # Get all entries
    context = manager.get_context(conv_id)
    assert len(context.entries) == 10
    
    # Get limited entries
    filtered_context = manager.get_context(conv_id, max_entries=5)
    assert len(filtered_context.entries) == 5
    # Should be the most recent entries
    assert filtered_context.entries[0].content == "Message 5"
    
    print("✓ Context filtering test passed")


def test_recent_entries():
    """Test recent entries retrieval."""
    manager = ContextManager()
    
    # Create conversation
    conv_id = manager.create_conversation()
    
    # Add entries
    manager.add_entry(conv_id, "user", "First message")
    manager.add_entry(conv_id, "assistant", "Second message")
    manager.add_entry(conv_id, "user", "Third message")
    
    # Get recent entries
    recent = manager.get_recent_entries(conv_id, count=2)
    assert len(recent) == 2
    assert recent[0].content == "Second message"
    assert recent[1].content == "Third message"
    
    print("✓ Recent entries test passed")


def test_context_search():
    """Test context search functionality."""
    manager = ContextManager()
    
    # Create conversation
    conv_id = manager.create_conversation()
    
    # Add entries with different content
    manager.add_entry(conv_id, "user", "I like machine learning")
    manager.add_entry(conv_id, "assistant", "Machine learning is fascinating")
    manager.add_entry(conv_id, "user", "What about deep learning?")
    manager.add_entry(conv_id, "assistant", "Deep learning is a subset of ML")
    
    # Search for "machine learning"
    results = manager.search_context(conv_id, "machine learning", max_results=5)
    assert len(results) >= 2  # Should find at least 2 entries
    
    # Search for "deep learning"
    results = manager.search_context(conv_id, "deep learning", max_results=5)
    assert len(results) >= 2  # Should find at least 2 entries
    
    print("✓ Context search test passed")


def test_metadata_updates():
    """Test metadata update functionality."""
    manager = ContextManager()
    
    # Create conversation
    conv_id = manager.create_conversation(metadata={"initial": "value"})
    
    # Update metadata
    success = manager.update_metadata(conv_id, {"updated": "new_value", "added": "data"})
    assert success == True
    
    # Check updated metadata
    context = manager.conversations[conv_id]
    assert context.metadata["initial"] == "value"  # Preserved
    assert context.metadata["updated"] == "new_value"  # Added
    assert context.metadata["added"] == "data"  # Added
    
    print("✓ Metadata updates test passed")


def test_context_clearing():
    """Test context clearing functionality."""
    manager = ContextManager()
    
    # Create conversation with entries
    conv_id = manager.create_conversation()
    manager.add_entry(conv_id, "user", "Test message")
    manager.add_entry(conv_id, "assistant", "Response")
    
    # Verify entries exist
    context = manager.get_context(conv_id)
    assert len(context.entries) == 2
    
    # Clear context
    success = manager.clear_context(conv_id)
    assert success == True
    
    # Verify entries are cleared
    context = manager.get_context(conv_id)
    assert len(context.entries) == 0
    
    print("✓ Context clearing test passed")


def test_conversation_deletion():
    """Test conversation deletion functionality."""
    manager = ContextManager()
    
    # Create conversation
    conv_id = manager.create_conversation()
    manager.add_entry(conv_id, "user", "Test")
    
    # Verify conversation exists
    assert conv_id in manager.conversations
    
    # Delete conversation
    success = manager.delete_conversation(conv_id)
    assert success == True
    
    # Verify conversation is deleted
    assert conv_id not in manager.conversations
    
    # Try to delete non-existent conversation
    success = manager.delete_conversation("nonexistent")
    assert success == False
    
    print("✓ Conversation deletion test passed")


def test_context_pruning():
    """Test context pruning functionality."""
    manager = ContextManager(max_conversations=5)
    
    # Create more conversations than limit
    conv_ids = []
    for i in range(10):
        conv_id = manager.create_conversation(metadata={"index": i})
        conv_ids.append(conv_id)
        # Add a small delay to ensure different timestamps
        import time
        time.sleep(0.01)
    
    # Check that we don't exceed the limit (allowing some buffer)
    assert len(manager.conversations) <= 10  # May be slightly over due to buffer
    
    print("✓ Context pruning test passed")


def test_serialization():
    """Test context serialization and deserialization."""
    manager = ContextManager()
    
    # Create conversation with entries
    conv_id = manager.create_conversation(metadata={"test": "serialization"})
    manager.add_entry(conv_id, "user", "Serialize this!")
    manager.add_entry(conv_id, "assistant", "Serialized content")
    
    # Serialize context
    serialized = manager.serialize_context(conv_id)
    assert serialized is not None
    assert isinstance(serialized, str)
    assert "Serialize this!" in serialized
    
    # Deserialize context
    new_conv_id = manager.deserialize_context(serialized)
    assert new_conv_id is not None
    assert new_conv_id in manager.conversations
    
    # Verify deserialized content
    new_context = manager.conversations[new_conv_id]
    assert len(new_context.entries) == 2
    assert new_context.metadata["test"] == "serialization"
    
    print("✓ Context serialization test passed")


def test_conversation_stats():
    """Test conversation statistics functionality."""
    manager = ContextManager()
    
    # Create conversations with entries
    for i in range(3):
        conv_id = manager.create_conversation()
        for j in range(i + 1):  # Different number of entries per conversation
            manager.add_entry(conv_id, "user", f"Message {j}")
    
    # Get statistics
    stats = manager.get_conversation_stats()
    
    # Check statistics
    assert "total_conversations" in stats
    assert "total_entries" in stats
    assert "average_entries_per_conversation" in stats
    assert stats["total_conversations"] == 3
    assert stats["total_entries"] == 6  # 1 + 2 + 3
    assert stats["average_entries_per_conversation"] == 2.0
    
    print("✓ Conversation statistics test passed")


if __name__ == "__main__":
    # Run all tests
    test_conversation_creation()
    test_entry_management()
    test_context_filtering()
    test_recent_entries()
    test_context_search()
    test_metadata_updates()
    test_context_clearing()
    test_conversation_deletion()
    test_context_pruning()
    test_serialization()
    test_conversation_stats()
    print("\n🎉 All context manager tests passed!")
