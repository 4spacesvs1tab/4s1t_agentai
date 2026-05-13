"""
Context Window Management for 4S1T Agent AI Framework.

This module provides intelligent context window management to prevent
overflow and handle large conversation histories efficiently.
"""

import asyncio
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ai.context.manager import ContextManager, ContextEntry, ConversationContext
from ai.models.base import BaseModel, ModelResponse
from components.health.monitor import HealthMonitor, HealthStatus, HealthCheckResult

from utils.logger import setup_logger
logger = setup_logger(__name__)


class CompactionStrategy(Enum):
    """Strategies for context compaction."""
    RECENT_ONLY = "recent_only"
    SUMMARY_BASED = "summary_based"
    PRIORITY_BASED = "priority_based"
    SEMANTIC_CLUSTERING = "semantic_clustering"


@dataclass
class ContextWindowConfig:
    """Configuration for context window management."""
    
    # Model-specific limits
    max_tokens: int = 8192  # Default for many models
    reserved_tokens: int = 512  # Tokens reserved for system prompts, etc.
    
    # Compaction settings
    auto_compaction_threshold: float = 0.8  # Trigger compaction at 80% usage
    compaction_target_ratio: float = 0.5  # Compact to 50% of max tokens
    compaction_strategy: CompactionStrategy = CompactionStrategy.PRIORITY_BASED
    
    # Semantic compaction settings
    summary_model_name: str = "gpt-3.5-turbo"  # Model for generating summaries
    min_entries_before_compaction: int = 10  # Minimum entries before compaction
    
    # Monitoring settings
    check_frequency_seconds: int = 30
    alert_threshold_percentage: float = 95.0  # Alert when approaching limit


@dataclass
class TokenUsage:
    """Token usage information."""
    
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    reserved_tokens: int
    available_tokens: int
    usage_percentage: float


class ContextWindowManager:
    """
    Manages context window limitations and prevents overflow.
    
    This class monitors token usage, automatically compacts context when needed,
    and provides recovery mechanisms for context overflow situations.
    """
    
    def __init__(self, context_manager: ContextManager, config: ContextWindowConfig = None):
        """
        Initialize the context window manager.
        
        Args:
            context_manager: Context manager to monitor
            config: Configuration for context window management
        """
        self.context_manager = context_manager
        self.config = config or ContextWindowConfig()
        self.token_counts: Dict[str, TokenUsage] = {}
        self.compaction_history: Dict[str, List[Dict[str, Any]]] = {}
        self.logger = logger
        
        # Register health check
        health_monitor = HealthMonitor.get_instance()
        health_monitor.register_health_check("context_window", self._health_check)
    
    async def estimate_token_usage(self, conversation_id: str, 
                                 model: BaseModel = None) -> TokenUsage:
        """
        Estimate token usage for a conversation context.
        
        Args:
            conversation_id: ID of the conversation
            model: Model to use for estimation (uses simple heuristic if None)
            
        Returns:
            TokenUsage: Estimated token usage
        """
        try:
            context = self.context_manager.get_context(conversation_id)
            if not context:
                return TokenUsage(0, 0, 0, self.config.reserved_tokens, 
                                self.config.max_tokens - self.config.reserved_tokens, 0.0)
            
            total_tokens = 0
            
            # Simple heuristic: ~4 characters per token
            # This is a rough approximation and should be replaced with actual tokenization
            for entry in context.entries:
                # Account for role markers and formatting
                entry_tokens = len(entry.content) // 4 + 10
                total_tokens += entry_tokens
            
            # Add system message overhead
            total_tokens += 100  # Approximate system message tokens
            
            # Add reserved tokens
            total_tokens += self.config.reserved_tokens
            
            available_tokens = max(0, self.config.max_tokens - total_tokens)
            usage_percentage = min(100.0, (total_tokens / self.config.max_tokens) * 100)
            
            token_usage = TokenUsage(
                total_tokens=total_tokens,
                prompt_tokens=total_tokens,  # Simplified
                completion_tokens=0,
                reserved_tokens=self.config.reserved_tokens,
                available_tokens=available_tokens,
                usage_percentage=usage_percentage
            )
            
            # Store for monitoring
            self.token_counts[conversation_id] = token_usage
            
            # Check if we need to alert
            if usage_percentage > self.config.alert_threshold_percentage:
                self.logger.warning(
                    f"High token usage for conversation {conversation_id}: "
                    f"{usage_percentage:.1f}% (threshold: {self.config.alert_threshold_percentage}%)"
                )
            
            return token_usage
            
        except Exception as e:
            self.logger.error(f"Error estimating token usage for conversation {conversation_id}: {e}")
            # Return safe defaults
            return TokenUsage(0, 0, 0, self.config.reserved_tokens,
                            self.config.max_tokens - self.config.reserved_tokens, 0.0)
    
    async def check_and_compact_context(self, conversation_id: str, 
                                      model: BaseModel = None) -> bool:
        """
        Check if context compaction is needed and perform it if necessary.
        
        Args:
            conversation_id: ID of the conversation
            model: Model to use for compaction (optional)
            
        Returns:
            bool: True if compaction was performed, False otherwise
        """
        try:
            token_usage = await self.estimate_token_usage(conversation_id, model)
            
            # Check if compaction is needed
            if (token_usage.usage_percentage < 
                self.config.auto_compaction_threshold * 100):
                return False
            
            # Check minimum entries requirement
            context = self.context_manager.get_context(conversation_id)
            if not context or len(context.entries) < self.config.min_entries_before_compaction:
                return False
            
            self.logger.info(
                f"Auto-compacting context for conversation {conversation_id} "
                f"(usage: {token_usage.usage_percentage:.1f}%)"
            )
            
            # Perform compaction based on strategy
            if self.config.compaction_strategy == CompactionStrategy.RECENT_ONLY:
                result = await self._compact_recent_only(conversation_id, model)
            elif self.config.compaction_strategy == CompactionStrategy.SUMMARY_BASED:
                result = await self._compact_summary_based(conversation_id, model)
            elif self.config.compaction_strategy == CompactionStrategy.PRIORITY_BASED:
                result = await self._compact_priority_based(conversation_id, model)
            elif self.config.compaction_strategy == CompactionStrategy.SEMANTIC_CLUSTERING:
                result = await self._compact_semantic_clustering(conversation_id, model)
            else:
                result = await self._compact_priority_based(conversation_id, model)
            
            if result:
                # Record compaction
                if conversation_id not in self.compaction_history:
                    self.compaction_history[conversation_id] = []
                
                self.compaction_history[conversation_id].append({
                    "timestamp": datetime.now().isoformat(),
                    "strategy": self.config.compaction_strategy.value,
                    "before_tokens": token_usage.total_tokens,
                    "after_tokens": result.get("final_tokens", 0),
                    "entries_removed": result.get("entries_removed", 0),
                    "entries_summarized": result.get("entries_summarized", 0)
                })
                
                self.logger.info(
                    f"Compaction completed for conversation {conversation_id}: "
                    f"removed {result.get('entries_removed', 0)} entries, "
                    f"summarized {result.get('entries_summarized', 0)} entries"
                )
            
            return result is not None
            
        except Exception as e:
            self.logger.error(f"Error during context compaction for conversation {conversation_id}: {e}")
            return False
    
    async def _compact_recent_only(self, conversation_id: str, 
                                 model: BaseModel = None) -> Optional[Dict[str, Any]]:
        """
        Compact context by keeping only recent entries.
        
        Args:
            conversation_id: ID of the conversation
            model: Model for compaction (unused in this strategy)
            
        Returns:
            Dict with compaction results or None if failed
        """
        try:
            context = self.context_manager.get_context(conversation_id)
            if not context:
                return None
            
            target_entries = max(2, int(len(context.entries) * self.config.compaction_target_ratio))
            
            # Keep most recent entries
            entries_to_keep = context.entries[-target_entries:]
            
            # Count removed entries
            entries_removed = len(context.entries) - len(entries_to_keep)
            
            # Update context
            context.entries = entries_to_keep
            
            # Estimate final token count
            final_usage = await self.estimate_token_usage(conversation_id, model)
            
            return {
                "entries_removed": entries_removed,
                "entries_summarized": 0,
                "final_tokens": final_usage.total_tokens
            }
            
        except Exception as e:
            self.logger.error(f"Error in recent-only compaction: {e}")
            return None
    
    async def _compact_summary_based(self, conversation_id: str, 
                                   model: BaseModel = None) -> Optional[Dict[str, Any]]:
        """
        Compact context by creating summaries of older entries.
        
        Args:
            conversation_id: ID of the conversation
            model: Model for generating summaries
            
        Returns:
            Dict with compaction results or None if failed
        """
        try:
            context = self.context_manager.get_context(conversation_id)
            if not context or len(context.entries) < 3:
                return None
            
            # Determine split point
            split_point = int(len(context.entries) * self.config.compaction_target_ratio)
            
            # Entries to summarize (older entries)
            entries_to_summarize = context.entries[:split_point]
            entries_to_keep = context.entries[split_point:]
            
            # Create summary of older entries
            summary_content = "\n".join([
                f"{entry.role}: {entry.content}" 
                for entry in entries_to_summarize
            ])
            
            summary_prompt = (
                "Summarize the following conversation history in a concise way "
                "that preserves the key information and context:\n\n"
                f"{summary_content}"
            )
            
            # Generate summary using model or fallback
            if model and model.is_loaded():
                try:
                    summary_response = await model.generate(
                        summary_prompt,
                        max_tokens=200,
                        temperature=0.3
                    )
                    summary_text = summary_response.content
                except Exception as e:
                    self.logger.warning(f"Failed to generate summary with model: {e}")
                    summary_text = f"Summary of {len(entries_to_summarize)} previous exchanges"
            else:
                summary_text = f"Summary of {len(entries_to_summarize)} previous exchanges"
            
            # Create summary entry
            summary_entry = ContextEntry(
                entry_id=f"summary_{datetime.now().timestamp()}",
                role="system",
                content=summary_text,
                metadata={"compaction_summary": True, "original_entries": len(entries_to_summarize)}
            )
            
            # Replace older entries with summary
            new_entries = [summary_entry] + entries_to_keep
            context.entries = new_entries
            
            # Estimate final token count
            final_usage = await self.estimate_token_usage(conversation_id, model)
            
            return {
                "entries_removed": len(entries_to_summarize),
                "entries_summarized": len(entries_to_summarize),
                "final_tokens": final_usage.total_tokens
            }
            
        except Exception as e:
            self.logger.error(f"Error in summary-based compaction: {e}")
            return None
    
    async def _compact_priority_based(self, conversation_id: str, 
                                    model: BaseModel = None) -> Optional[Dict[str, Any]]:
        """
        Compact context by prioritizing important entries.
        
        Args:
            conversation_id: ID of the conversation
            model: Model for analysis (optional)
            
        Returns:
            Dict with compaction results or None if failed
        """
        try:
            context = self.context_manager.get_context(conversation_id)
            if not context:
                return None
            
            # Assign priorities to entries
            prioritized_entries = []
            for entry in context.entries:
                priority = self._calculate_entry_priority(entry)
                prioritized_entries.append((priority, entry))
            
            # Sort by priority (highest first)
            prioritized_entries.sort(reverse=True)
            
            # Keep high-priority entries and some recent ones
            target_count = max(3, int(len(context.entries) * self.config.compaction_target_ratio))
            
            # Always keep recent entries (last 20%)
            recent_count = max(1, int(len(context.entries) * 0.2))
            recent_entries = context.entries[-recent_count:]
            
            # Select high-priority entries
            priority_entries = [entry for _, entry in prioritized_entries[:target_count-len(recent_entries)]]
            
            # Combine and deduplicate
            selected_entries = list(set(priority_entries + recent_entries))
            
            # Sort by timestamp to maintain order
            selected_entries.sort(key=lambda x: x.timestamp)
            
            # Count removed entries
            entries_removed = len(context.entries) - len(selected_entries)
            
            # Update context
            context.entries = selected_entries
            
            # Estimate final token count
            final_usage = await self.estimate_token_usage(conversation_id, model)
            
            return {
                "entries_removed": entries_removed,
                "entries_summarized": 0,
                "final_tokens": final_usage.total_tokens
            }
            
        except Exception as e:
            self.logger.error(f"Error in priority-based compaction: {e}")
            return None
    
    def _calculate_entry_priority(self, entry: ContextEntry) -> float:
        """
        Calculate priority score for a context entry.
        
        Args:
            entry: Context entry to score
            
        Returns:
            float: Priority score (higher is more important)
        """
        priority = 1.0
        
        # Boost for assistant responses (likely contain important information)
        if entry.role == "assistant":
            priority *= 1.5
        
        # Boost for entries with specific keywords
        important_keywords = ["important", "critical", "urgent", "summary", "conclusion"]
        content_lower = entry.content.lower()
        for keyword in important_keywords:
            if keyword in content_lower:
                priority *= 1.2
        
        # Boost for entries with metadata indicating importance
        if entry.metadata.get("importance", 0) > 0:
            priority *= (1.0 + entry.metadata.get("importance", 0) / 10.0)
        
        # Recency boost (more recent entries are generally more relevant)
        hours_old = (datetime.now() - entry.timestamp).total_seconds() / 3600
        recency_boost = max(0.5, 2.0 - (hours_old / 24.0))  # Decreases over 48 hours
        priority *= recency_boost
        
        return priority
    
    async def _compact_semantic_clustering(self, conversation_id: str, 
                                         model: BaseModel = None) -> Optional[Dict[str, Any]]:
        """
        Compact context by clustering semantically similar entries.
        
        Args:
            conversation_id: ID of the conversation
            model: Model for semantic analysis (optional)
            
        Returns:
            Dict with compaction results or None if failed
        """
        try:
            context = self.context_manager.get_context(conversation_id)
            if not context or len(context.entries) < 3:
                return None
            
            # For semantic clustering, we'll use a simple approach:
            # Group entries by similarity and keep representative entries from each cluster
            entries = context.entries
            target_count = max(3, int(len(entries) * self.config.compaction_target_ratio))
            
            if len(entries) <= target_count:
                return None  # No compaction needed
            
            # Simple clustering approach: group by role and content similarity
            clusters = self._cluster_entries_by_similarity(entries)
            
            # Select representative entries from each cluster
            selected_entries = []
            
            # Always keep the most recent entries (last 20%)
            recent_count = max(1, int(len(entries) * 0.2))
            recent_entries = entries[-recent_count:]
            selected_entries.extend(recent_entries)
            
            # From each cluster, select the most important entry
            for cluster in clusters:
                if len(selected_entries) >= target_count:
                    break
                
                # Select the highest priority entry from the cluster
                prioritized_cluster = [(self._calculate_entry_priority(entry), entry) for entry in cluster]
                prioritized_cluster.sort(reverse=True)
                
                # Add the highest priority entry from this cluster
                if prioritized_cluster:
                    selected_entries.append(prioritized_cluster[0][1])
            
            # Deduplicate and sort by timestamp
            selected_entries = list(set(selected_entries))
            selected_entries.sort(key=lambda x: x.timestamp)
            
            # Limit to target count
            if len(selected_entries) > target_count:
                # Re-prioritize and trim
                prioritized_selected = [(self._calculate_entry_priority(entry), entry) for entry in selected_entries]
                prioritized_selected.sort(reverse=True)
                selected_entries = [entry for _, entry in prioritized_selected[:target_count]]
                selected_entries.sort(key=lambda x: x.timestamp)
            
            # Count removed entries
            entries_removed = len(entries) - len(selected_entries)
            
            # Update context
            context.entries = selected_entries
            
            # Estimate final token count
            final_usage = await self.estimate_token_usage(conversation_id, model)
            
            return {
                "entries_removed": entries_removed,
                "entries_summarized": 0,  # Clustering doesn't summarize, just selects
                "final_tokens": final_usage.total_tokens
            }
            
        except Exception as e:
            self.logger.error(f"Error in semantic clustering compaction: {e}")
            return None
    
    def _cluster_entries_by_similarity(self, entries: List[ContextEntry]) -> List[List[ContextEntry]]:
        """
        Cluster entries by semantic similarity (simplified approach).
        
        Args:
            entries: List of context entries to cluster
            
        Returns:
            List of clusters (each cluster is a list of entries)
        """
        if len(entries) < 2:
            return [entries] if entries else []
        
        clusters = []
        
        # Simple clustering based on content overlap and role similarity
        # In a real implementation, this would use embeddings
        for entry in entries:
            # Try to find an existing cluster
            added_to_cluster = False
            
            for cluster in clusters:
                # Check if this entry belongs to the cluster
                if self._entries_are_similar(entry, cluster[0]):
                    cluster.append(entry)
                    added_to_cluster = True
                    break
            
            # If no suitable cluster found, create a new one
            if not added_to_cluster:
                clusters.append([entry])
        
        return clusters
    
    def _entries_are_similar(self, entry1: ContextEntry, entry2: ContextEntry) -> bool:
        """
        Determine if two entries are semantically similar (simplified approach).
        
        Args:
            entry1: First entry
            entry2: Second entry
            
        Returns:
            bool: True if entries are considered similar
        """
        # Simple similarity check based on:
        # 1. Same role
        # 2. Content overlap (keyword matching)
        # 3. Temporal proximity
        
        # Same role increases similarity
        if entry1.role == entry2.role:
            role_similarity = 1.0
        else:
            role_similarity = 0.5
        
        # Content overlap
        words1 = set(entry1.content.lower().split())
        words2 = set(entry2.content.lower().split())
        
        if not words1 or not words2:
            content_similarity = 0.0
        else:
            intersection = len(words1.intersection(words2))
            union = len(words1.union(words2))
            content_similarity = intersection / union if union > 0 else 0.0
        
        # Temporal proximity (entries close in time are more likely related)
        time_diff = abs((entry1.timestamp - entry2.timestamp).total_seconds())
        # Consider entries within 10 minutes as temporally close
        temporal_similarity = max(0.0, 1.0 - (time_diff / 600.0))
        
        # Combined similarity score
        combined_similarity = (role_similarity * 0.3 + 
                             content_similarity * 0.5 + 
                             temporal_similarity * 0.2)
        
        # Threshold for considering entries similar
        return combined_similarity > 0.3
    
    def get_compaction_history(self, conversation_id: str) -> List[Dict[str, Any]]:
        """
        Get compaction history for a conversation.
        
        Args:
            conversation_id: ID of the conversation
            
        Returns:
            List of compaction events
        """
        return self.compaction_history.get(conversation_id, [])
    
    def _health_check(self) -> HealthCheckResult:
        """
        Perform health check for context window management.
        
        Returns:
            HealthCheckResult: Health check result
        """
        try:
            # Check for any conversations with high token usage
            high_usage_conversations = []
            for conv_id, token_usage in self.token_counts.items():
                if token_usage.usage_percentage > self.config.alert_threshold_percentage:
                    high_usage_conversations.append({
                        "conversation_id": conv_id,
                        "usage_percentage": token_usage.usage_percentage
                    })
            
            if high_usage_conversations:
                return HealthCheckResult(
                    component="context_window",
                    status=HealthStatus.DEGRADED,
                    message=f"{len(high_usage_conversations)} conversations exceeding token threshold",
                    details={"high_usage_conversations": high_usage_conversations}
                )
            else:
                return HealthCheckResult(
                    component="context_window",
                    status=HealthStatus.HEALTHY,
                    message="Context window management operating normally"
                )
                
        except Exception as e:
            return HealthCheckResult(
                component="context_window",
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}"
            )
