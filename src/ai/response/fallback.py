"""
Fallback response handling system for the 4S1T Agent AI framework.

This module provides functionality for handling AI model failures and providing
fallback responses to ensure system reliability and user experience.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union, Callable
from datetime import datetime
import logging
import random

from ..models.base import ModelResponse
from .processor import ProcessedResponse, ResponseValidationRule, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class FallbackStrategy:
    """Strategy for handling fallback responses."""
    
    name: str
    description: str = ""
    priority: int = 0  # Higher priority = used first
    conditions: List[str] = field(default_factory=list)  # Conditions when to use
    response_generator: Optional[Callable[..., str]] = None
    static_responses: List[str] = field(default_factory=list)
    use_previous_context: bool = False
    max_retries: int = 3
    timeout_seconds: float = 30.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FallbackEvent:
    """Record of a fallback event."""
    
    event_id: str
    original_request: str
    error_type: str
    error_message: str
    fallback_strategy_used: str
    fallback_response: str
    timestamp: datetime = field(default_factory=datetime.now)
    retry_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class FallbackHandler:
    """
    Handler for fallback responses in the 4S1T Agent AI framework.
    
    This class provides mechanisms for gracefully handling AI model failures
    and providing appropriate fallback responses.
    """
    
    def __init__(self):
        """Initialize the fallback handler."""
        self.strategies: Dict[str, FallbackStrategy] = {}
        self.fallback_events: List[FallbackEvent] = []
        self.retry_counts: Dict[str, int] = {}  # Track retries per request
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def register_strategy(self, strategy: FallbackStrategy) -> bool:
        """
        Register a fallback strategy.
        
        Args:
            strategy: The fallback strategy to register
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            self.strategies[strategy.name] = strategy
            self.logger.info(f"Registered fallback strategy: {strategy.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register fallback strategy {strategy.name}: {e}")
            return False
    
    def handle_failure(self, request: str, error: Exception, 
                      context: Optional[Dict[str, Any]] = None) -> str:
        """
        Handle a model failure and provide a fallback response.
        
        Args:
            request: Original request that failed
            error: Exception that occurred
            context: Additional context information
            
        Returns:
            str: Fallback response
        """
        try:
            event_id = f"fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"
            
            # Get error information
            error_type = type(error).__name__
            error_message = str(error)
            
            self.logger.warning(f"Fallback triggered for request: {request[:50]}... Error: {error_type}")
            
            # Increment retry count
            retry_count = self.retry_counts.get(request, 0) + 1
            self.retry_counts[request] = retry_count
            
            # Select appropriate fallback strategy
            strategy = self._select_strategy(error_type, context)
            
            # Generate fallback response
            fallback_response = self._generate_fallback_response(strategy, request, context, retry_count)
            
            # Record fallback event
            event = FallbackEvent(
                event_id=event_id,
                original_request=request,
                error_type=error_type,
                error_message=error_message,
                fallback_strategy_used=strategy.name if strategy else "none",
                fallback_response=fallback_response,
                retry_count=retry_count,
                metadata=context or {}
            )
            self.fallback_events.append(event)
            
            self.logger.info(f"Provided fallback response using strategy: {strategy.name if strategy else 'none'}")
            return fallback_response
            
        except Exception as e:
            self.logger.error(f"Failed to handle fallback: {e}")
            # Ultimate fallback
            return "I'm sorry, I'm experiencing technical difficulties right now. Please try again later."
    
    def _select_strategy(self, error_type: str, 
                        context: Optional[Dict[str, Any]] = None) -> Optional[FallbackStrategy]:
        """
        Select the most appropriate fallback strategy based on error and context.
        
        Args:
            error_type: Type of error that occurred
            context: Additional context information
            
        Returns:
            FallbackStrategy: Selected strategy, or None if none found
        """
        # Sort strategies by priority (highest first)
        sorted_strategies = sorted(self.strategies.values(), key=lambda s: s.priority, reverse=True)
        
        # Check conditions for each strategy
        for strategy in sorted_strategies:
            # If no conditions, consider it a general fallback
            if not strategy.conditions:
                return strategy
            
            # Check if any condition matches
            for condition in strategy.conditions:
                # Simple condition matching - in practice, this could be more sophisticated
                if (condition.lower() in error_type.lower() or 
                    (context and condition.lower() in str(context).lower())):
                    return strategy
        
        # Return highest priority strategy if no conditions match
        return sorted_strategies[0] if sorted_strategies else None
    
    def _generate_fallback_response(self, strategy: Optional[FallbackStrategy], 
                                  request: str, context: Optional[Dict[str, Any]], 
                                  retry_count: int) -> str:
        """
        Generate a fallback response using the selected strategy.
        
        Args:
            strategy: Fallback strategy to use
            request: Original request
            context: Additional context
            retry_count: Number of retry attempts
            
        Returns:
            str: Generated fallback response
        """
        if not strategy:
            return "I'm sorry, I'm having trouble processing your request right now."
        
        # Check retry limits
        if retry_count > strategy.max_retries:
            return "I've tried multiple times but am still unable to process your request. Please try again later."
        
        # Use custom generator if available
        if strategy.response_generator:
            try:
                return strategy.response_generator(request, context, retry_count)
            except Exception as e:
                self.logger.warning(f"Custom generator failed: {e}")
        
        # Use static responses
        if strategy.static_responses:
            # Select response based on retry count or randomly
            if retry_count <= len(strategy.static_responses):
                return strategy.static_responses[retry_count - 1]
            else:
                return random.choice(strategy.static_responses)
        
        # Default fallback
        return "I'm sorry, I'm experiencing technical difficulties right now."
    
    def handle_validation_failure(self, processed_response: ProcessedResponse,
                                validation_results: List[ValidationResult],
                                rules: List[ResponseValidationRule]) -> str:
        """
        Handle response validation failures.
        
        Args:
            processed_response: The response that failed validation
            validation_results: Validation results
            rules: Validation rules that were applied
            
        Returns:
            str: Fallback response for validation failure
        """
        try:
            # Log validation failure
            self.logger.warning(f"Response validation failed: {[v.value for v in validation_results]}")
            
            # Create error context
            context = {
                "validation_results": [v.value for v in validation_results],
                "format_detected": processed_response.format_detected.value,
                "confidence_score": processed_response.confidence_score
            }
            
            # Handle specific validation failures
            if ValidationResult.TOO_LONG in validation_results:
                return "My response was too lengthy. Let me provide a more concise answer."
            
            elif ValidationResult.TOO_SHORT in validation_results:
                return "My response was incomplete. Let me try to provide more information."
            
            elif ValidationResult.CONTAINS_FORBIDDEN in validation_results:
                return "I need to be more careful with my response content. Let me rephrase that."
            
            elif ValidationResult.INVALID_FORMAT in validation_results:
                return "I had trouble formatting my response properly. Let me try again."
            
            else:
                return "I need to improve my response quality. Let me reconsider my answer."
                
        except Exception as e:
            self.logger.error(f"Failed to handle validation failure: {e}")
            return "I need to improve my response quality. Please bear with me."
    
    def get_fallback_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about fallback usage.
        
        Returns:
            Dict[str, Any]: Statistics about fallback events
        """
        if not self.fallback_events:
            return {"total_fallbacks": 0}
        
        # Count by strategy
        strategy_counts = {}
        error_counts = {}
        retry_counts = []
        
        for event in self.fallback_events:
            # Strategy counts
            strategy = event.fallback_strategy_used
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
            
            # Error counts
            error_type = event.error_type
            error_counts[error_type] = error_counts.get(error_type, 0) + 1
            
            # Retry counts
            retry_counts.append(event.retry_count)
        
        return {
            "total_fallbacks": len(self.fallback_events),
            "strategies_used": strategy_counts,
            "errors_handled": error_counts,
            "average_retries": sum(retry_counts) / len(retry_counts) if retry_counts else 0,
            "max_retries": max(retry_counts) if retry_counts else 0
        }
    
    def clear_retry_counts(self, request: Optional[str] = None):
        """
        Clear retry counts, optionally for a specific request.
        
        Args:
            request: Specific request to clear count for, or None to clear all
        """
        if request:
            if request in self.retry_counts:
                del self.retry_counts[request]
        else:
            self.retry_counts.clear()
    
    def get_recent_events(self, limit: int = 10) -> List[FallbackEvent]:
        """
        Get recent fallback events.
        
        Args:
            limit: Maximum number of events to return
            
        Returns:
            List[FallbackEvent]: Recent fallback events
        """
        return sorted(self.fallback_events, key=lambda e: e.timestamp, reverse=True)[:limit]


# Default fallback strategies
DEFAULT_FALLBACK_STRATEGIES = [
    FallbackStrategy(
        name="timeout_recovery",
        description="Handle timeout-related failures",
        priority=10,
        conditions=["timeout", "deadline", "time"],
        static_responses=[
            "I'm taking a bit longer to process your request. Let me think...",
            "Still working on your request, almost there...",
            "Sorry for the delay. I'm still processing your request."
        ],
        max_retries=2
    ),
    FallbackStrategy(
        name="connection_recovery",
        description="Handle connection-related failures",
        priority=8,
        conditions=["connection", "network", "socket"],
        static_responses=[
            "I'm having trouble connecting to my knowledge base. Let me try again.",
            "Connection issue detected. Attempting to reconnect...",
            "Network problems encountered. Retrying connection."
        ]
    ),
    FallbackStrategy(
        name="content_safety",
        description="Handle content safety violations",
        priority=9,
        conditions=["safety", "filter", "blocked"],
        static_responses=[
            "I need to be more careful with my response. Let me rephrase that appropriately.",
            "I want to make sure my response is appropriate. Let me reconsider.",
            "I'm adjusting my response to be more helpful and appropriate."
        ]
    ),
    FallbackStrategy(
        name="general_fallback",
        description="General purpose fallback for unspecified errors",
        priority=1,
        static_responses=[
            "I'm sorry, I'm experiencing some technical difficulties right now.",
            "I'm having trouble processing your request. Please try again.",
            "Something went wrong on my end. I apologize for the inconvenience."
        ]
    )
]


def initialize_default_strategies(handler: FallbackHandler) -> bool:
    """
    Initialize the handler with default fallback strategies.
    
    Args:
        handler: The fallback handler to initialize
        
    Returns:
        bool: True if initialization was successful, False otherwise
    """
    try:
        for strategy in DEFAULT_FALLBACK_STRATEGIES:
            handler.register_strategy(strategy)
        return True
    except Exception as e:
        logging.error(f"Failed to initialize default strategies: {e}")
        return False


# Example usage
if __name__ == "__main__":
    # Create fallback handler
    handler = FallbackHandler()
    initialize_default_strategies(handler)
    
    # Simulate a timeout error
    try:
        raise TimeoutError("Request timed out after 30 seconds")
    except Exception as e:
        fallback_response = handler.handle_failure(
            request="What is the meaning of life?",
            error=e,
            context={"user_id": "12345", "session_id": "abcde"}
        )
        print(f"Timeout fallback: {fallback_response}")
    
    # Simulate a connection error
    try:
        raise ConnectionError("Failed to connect to model service")
    except Exception as e:
        fallback_response = handler.handle_failure(
            request="Generate a story about dragons",
            error=e
        )
        print(f"Connection fallback: {fallback_response}")
    
    # Show statistics
    stats = handler.get_fallback_statistics()
    print(f"Fallback statistics: {stats}")
