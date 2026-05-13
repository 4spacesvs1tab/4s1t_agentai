"""
Conversation context management system for the 4S1T Agent AI framework.

This module provides functionality for managing conversation context,
including storage, retrieval, pruning, and multi-turn conversation support.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timedelta
import json
import uuid

from utils.logger import setup_logger
logger = setup_logger(__name__)


@dataclass
class ContextEntry:
    """A single entry in the conversation context."""
    
    entry_id: str
    role: str  # "user", "assistant", "system", etc.
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None  # For semantic search


@dataclass
class ConversationContext:
    """Complete context for a conversation."""
    
    conversation_id: str
    entries: List[ContextEntry] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    max_entries: int = 100  # Maximum number of entries to keep
    max_age_hours: int = 24  # Maximum age of entries in hours


class ContextManager:
    """
    Manager for conversation context in the 4S1T Agent AI framework.
    
    This class provides functionality for storing, retrieving, and managing
    conversation context across multiple turns.
    """
    
    def __init__(self, max_conversations: int = 1000):
        """
        Initialize the context manager.
        
        Args:
            max_conversations: Maximum number of conversations to keep in memory
        """
        self.conversations: Dict[str, ConversationContext] = {}
        self.max_conversations = max_conversations
        self.logger = logger
    
    def create_conversation(self, conversation_id: Optional[str] = None,
                          metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Create a new conversation context.
        
        Args:
            conversation_id: ID for the conversation (generated if None)
            metadata: Additional metadata for the conversation
            
        Returns:
            str: Conversation ID
        """
        try:
            if conversation_id is None:
                conversation_id = str(uuid.uuid4())
            
            # Prune old conversations if we're at capacity
            if len(self.conversations) >= self.max_conversations:
                self._prune_old_conversations()
            
            # Create new conversation
            context = ConversationContext(
                conversation_id=conversation_id,
                metadata=metadata or {}
            )
            
            self.conversations[conversation_id] = context
            self.logger.info(f"Created conversation: {conversation_id}")
            return conversation_id
            
        except Exception as e:
            self.logger.error(f"Failed to create conversation: {e}")
            raise
    
    def add_entry(self, conversation_id: str, role: str, content: str,
                 metadata: Optional[Dict[str, Any]] = None,
                 embedding: Optional[List[float]] = None) -> str:
        """
        Add an entry to a conversation context.
        
        Args:
            conversation_id: ID of the conversation
            role: Role of the entry (user, assistant, system, etc.)
            content: Content of the entry
            metadata: Additional metadata for the entry
            embedding: Embedding vector for semantic search
            
        Returns:
            str: Entry ID
        """
        try:
            # Get conversation context
            if conversation_id not in self.conversations:
                self.create_conversation(conversation_id)
            
            context = self.conversations[conversation_id]
            context.last_accessed = datetime.now()
            
            # Create entry
            entry_id = str(uuid.uuid4())
            entry = ContextEntry(
                entry_id=entry_id,
                role=role,
                content=content,
                metadata=metadata or {},
                embedding=embedding
            )
            
            # Add entry
            context.entries.append(entry)
            
            # Prune old entries if needed
            self._prune_context_entries(context)
            
            self.logger.debug(f"Added entry {entry_id} to conversation {conversation_id}")
            return entry_id
            
        except Exception as e:
            self.logger.error(f"Failed to add entry to conversation {conversation_id}: {e}")
            raise
    
    def get_context(self, conversation_id: str, 
                   max_entries: Optional[int] = None,
                   max_age_hours: Optional[int] = None) -> Optional[ConversationContext]:
        """
        Get conversation context.
        
        Args:
            conversation_id: ID of the conversation
            max_entries: Maximum number of entries to return
            max_age_hours: Maximum age of entries to include
            
        Returns:
            ConversationContext: Conversation context, or None if not found
        """
        try:
            if conversation_id not in self.conversations:
                return None
            
            context = self.conversations[conversation_id]
            context.last_accessed = datetime.now()
            
            # Apply filters if specified
            if max_entries is not None or max_age_hours is not None:
                filtered_context = self._filter_context(context, max_entries, max_age_hours)
                return filtered_context
            
            return context
            
        except Exception as e:
            self.logger.error(f"Failed to get context for conversation {conversation_id}: {e}")
            return None
    
    def _filter_context(self, context: ConversationContext,
                       max_entries: Optional[int] = None,
                       max_age_hours: Optional[int] = None) -> ConversationContext:
        """
        Filter context entries based on criteria.
        
        Args:
            context: Original context
            max_entries: Maximum number of entries to include
            max_age_hours: Maximum age of entries to include
            
        Returns:
            ConversationContext: Filtered context
        """
        # Start with a copy of the original context
        filtered_context = ConversationContext(
            conversation_id=context.conversation_id,
            created_at=context.created_at,
            last_accessed=context.last_accessed,
            metadata=context.metadata.copy(),
            max_entries=context.max_entries,
            max_age_hours=context.max_age_hours
        )
        
        # Filter entries
        entries = context.entries.copy()
        
        # Apply age filter
        if max_age_hours is not None:
            cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
            entries = [entry for entry in entries if entry.timestamp >= cutoff_time]
        
        # Apply count filter
        if max_entries is not None and len(entries) > max_entries:
            entries = entries[-max_entries:]  # Keep most recent entries
        
        filtered_context.entries = entries
        return filtered_context
    
    def get_recent_entries(self, conversation_id: str, 
                          count: int = 5) -> List[ContextEntry]:
        """
        Get recent entries from a conversation.
        
        Args:
            conversation_id: ID of the conversation
            count: Number of recent entries to return
            
        Returns:
            List[ContextEntry]: Recent entries
        """
        context = self.get_context(conversation_id, max_entries=count)
        if context:
            return context.entries
        return []
    
    def search_context(self, conversation_id: str, 
                      query: str,
                      max_results: int = 5) -> List[ContextEntry]:
        """
        Search conversation context for relevant entries.
        
        Args:
            conversation_id: ID of the conversation
            query: Search query
            max_results: Maximum number of results to return
            
        Returns:
            List[ContextEntry]: Relevant entries
        """
        try:
            if conversation_id not in self.conversations:
                return []
            
            context = self.conversations[conversation_id]
            context.last_accessed = datetime.now()
            
            # Simple text-based search (in a real implementation, this would use embeddings)
            results = []
            query_lower = query.lower()
            
            for entry in context.entries:
                if (query_lower in entry.content.lower() or
                    query_lower in entry.role.lower() or
                    any(query_lower in str(v).lower() for v in entry.metadata.values())):
                    results.append(entry)
            
            # Return most recent matches first
            results.sort(key=lambda x: x.timestamp, reverse=True)
            return results[:max_results]
            
        except Exception as e:
            self.logger.error(f"Failed to search context for conversation {conversation_id}: {e}")
            return []
    
    def update_metadata(self, conversation_id: str, 
                       metadata: Dict[str, Any]) -> bool:
        """
        Update conversation metadata.
        
        Args:
            conversation_id: ID of the conversation
            metadata: Metadata to update
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        try:
            if conversation_id not in self.conversations:
                return False
            
            context = self.conversations[conversation_id]
            context.metadata.update(metadata)
            context.last_accessed = datetime.now()
            
            self.logger.debug(f"Updated metadata for conversation {conversation_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to update metadata for conversation {conversation_id}: {e}")
            return False
    
    def clear_context(self, conversation_id: str) -> bool:
        """
        Clear all entries from a conversation context.
        
        Args:
            conversation_id: ID of the conversation
            
        Returns:
            bool: True if clear was successful, False otherwise
        """
        try:
            if conversation_id not in self.conversations:
                return False
            
            context = self.conversations[conversation_id]
            context.entries.clear()
            context.last_accessed = datetime.now()
            
            self.logger.info(f"Cleared context for conversation {conversation_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to clear context for conversation {conversation_id}: {e}")
            return False
    
    def delete_conversation(self, conversation_id: str) -> bool:
        """
        Delete a conversation context.
        
        Args:
            conversation_id: ID of the conversation
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        try:
            if conversation_id not in self.conversations:
                return False
            
            del self.conversations[conversation_id]
            self.logger.info(f"Deleted conversation {conversation_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to delete conversation {conversation_id}: {e}")
            return False
    
    def _prune_context_entries(self, context: ConversationContext):
        """
        Prune old or excessive entries from a conversation context.
        
        Args:
            context: Conversation context to prune
        """
        try:
            # Remove entries that are too old
            if context.max_age_hours > 0:
                cutoff_time = datetime.now() - timedelta(hours=context.max_age_hours)
                context.entries = [entry for entry in context.entries if entry.timestamp >= cutoff_time]
            
            # Remove excess entries
            if context.max_entries > 0 and len(context.entries) > context.max_entries:
                # Keep the most recent entries
                context.entries = context.entries[-context.max_entries:]
                
        except Exception as e:
            self.logger.warning(f"Failed to prune context entries: {e}")
    
    def _prune_old_conversations(self):
        """
        Prune old conversations to stay within memory limits.
        """
        try:
            # Sort conversations by last accessed time
            sorted_convs = sorted(self.conversations.items(), 
                                key=lambda x: x[1].last_accessed)
            
            # Remove oldest conversations
            to_remove = len(self.conversations) - self.max_conversations + 10  # Leave some buffer
            if to_remove > 0:
                for conversation_id, _ in sorted_convs[:to_remove]:
                    del self.conversations[conversation_id]
                    self.logger.debug(f"Pruned old conversation: {conversation_id}")
                    
        except Exception as e:
            self.logger.warning(f"Failed to prune old conversations: {e}")
    
    def get_conversation_stats(self) -> Dict[str, Any]:
        """
        Get statistics about conversation contexts.
        
        Returns:
            Dict[str, Any]: Statistics about conversations
        """
        try:
            total_conversations = len(self.conversations)
            total_entries = sum(len(ctx.entries) for ctx in self.conversations.values())
            
            # Find oldest and newest conversations
            if self.conversations:
                oldest = min(ctx.last_accessed for ctx in self.conversations.values())
                newest = max(ctx.last_accessed for ctx in self.conversations.values())
            else:
                oldest = newest = None
            
            return {
                "total_conversations": total_conversations,
                "total_entries": total_entries,
                "average_entries_per_conversation": total_entries / total_conversations if total_conversations > 0 else 0,
                "oldest_conversation_accessed": oldest.isoformat() if oldest else None,
                "newest_conversation_accessed": newest.isoformat() if newest else None
            }
            
        except Exception as e:
            self.logger.error(f"Failed to get conversation stats: {e}")
            return {"error": str(e)}
    
    def serialize_context(self, conversation_id: str) -> Optional[str]:
        """
        Serialize conversation context to JSON string.
        
        Args:
            conversation_id: ID of the conversation
            
        Returns:
            str: Serialized context, or None if conversation not found
        """
        try:
            context = self.get_context(conversation_id)
            if not context:
                return None
            
            # Convert to serializable format
            data = {
                "conversation_id": context.conversation_id,
                "entries": [
                    {
                        "entry_id": entry.entry_id,
                        "role": entry.role,
                        "content": entry.content,
                        "timestamp": entry.timestamp.isoformat(),
                        "metadata": entry.metadata,
                        "embedding": entry.embedding
                    }
                    for entry in context.entries
                ],
                "created_at": context.created_at.isoformat(),
                "last_accessed": context.last_accessed.isoformat(),
                "metadata": context.metadata,
                "max_entries": context.max_entries,
                "max_age_hours": context.max_age_hours
            }
            
            return json.dumps(data, indent=2)
            
        except Exception as e:
            self.logger.error(f"Failed to serialize context for conversation {conversation_id}: {e}")
            return None
    
    def deserialize_context(self, serialized_data: str) -> Optional[str]:
        """
        Deserialize conversation context from JSON string.
        
        Args:
            serialized_data: JSON string of serialized context
            
        Returns:
            str: Conversation ID, or None if deserialization failed
        """
        try:
            data = json.loads(serialized_data)
            
            # Create conversation context
            context = ConversationContext(
                conversation_id=data["conversation_id"],
                created_at=datetime.fromisoformat(data["created_at"]),
                last_accessed=datetime.fromisoformat(data["last_accessed"]),
                metadata=data["metadata"],
                max_entries=data["max_entries"],
                max_age_hours=data["max_age_hours"]
            )
            
            # Restore entries
            for entry_data in data["entries"]:
                entry = ContextEntry(
                    entry_id=entry_data["entry_id"],
                    role=entry_data["role"],
                    content=entry_data["content"],
                    timestamp=datetime.fromisoformat(entry_data["timestamp"]),
                    metadata=entry_data["metadata"],
                    embedding=entry_data["embedding"]
                )
                context.entries.append(entry)
            
            # Store context
            self.conversations[context.conversation_id] = context
            
            self.logger.info(f"Deserialized conversation: {context.conversation_id}")
            return context.conversation_id
            
        except Exception as e:
            self.logger.error(f"Failed to deserialize context: {e}")
            return None


# Example usage
if __name__ == "__main__":
    # Create context manager
    manager = ContextManager()
    
    # Create a conversation
    conv_id = manager.create_conversation(metadata={"user_id": "12345", "topic": "AI"})
    print(f"Created conversation: {conv_id}")
    
    # Add entries
    manager.add_entry(conv_id, "user", "Hello, what can you help me with?")
    manager.add_entry(conv_id, "assistant", "I can help you with various AI-related questions!")
    manager.add_entry(conv_id, "user", "Can you explain machine learning?")
    manager.add_entry(conv_id, "assistant", "Machine learning is a subset of AI that focuses on algorithms...")
    
    # Get context
    context = manager.get_context(conv_id)
    if context:
        print(f"Conversation has {len(context.entries)} entries")
        for entry in context.entries:
            print(f"  {entry.role}: {entry.content[:50]}...")
    
    # Get recent entries
    recent = manager.get_recent_entries(conv_id, count=2)
    print(f"\nRecent entries ({len(recent)}):")
    for entry in recent:
        print(f"  {entry.role}: {entry.content}")
    
    # Search context
    results = manager.search_context(conv_id, "machine learning")
    print(f"\nSearch results ({len(results)}):")
    for entry in results:
        print(f"  {entry.role}: {entry.content}")
    
    # Get statistics
    stats = manager.get_conversation_stats()
    print(f"\nStatistics: {stats}")
