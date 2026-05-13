"""
Tests for the fallback response handling system.
"""

import sys
import os

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.response.fallback import (
    FallbackHandler,
    FallbackStrategy,
    initialize_default_strategies
)


def test_strategy_registration():
    """Test fallback strategy registration."""
    handler = FallbackHandler()
    
    # Create a test strategy
    strategy = FallbackStrategy(
        name="test_strategy",
        description="Test fallback strategy",
        priority=5,
        static_responses=["Test fallback response"]
    )
    
    # Register strategy
    assert handler.register_strategy(strategy) == True
    
    # Check that strategy is registered
    assert "test_strategy" in handler.strategies
    assert handler.strategies["test_strategy"].priority == 5
    
    print("✓ Strategy registration test passed")


def test_failure_handling():
    """Test failure handling functionality."""
    handler = FallbackHandler()
    initialize_default_strategies(handler)
    
    # Simulate a timeout error
    try:
        raise TimeoutError("Request timed out")
    except Exception as e:
        fallback_response = handler.handle_failure(
            request="Test request",
            error=e,
            context={"test": "context"}
        )
    
    # Check that we got a fallback response
    assert isinstance(fallback_response, str)
    assert len(fallback_response) > 0
    # Check for reasonable fallback content
    assert any(word in fallback_response.lower() for word in ["sorry", "trouble", "longer", "think", "delay"])
    
    print("✓ Failure handling test passed")


def test_timeout_fallback():
    """Test timeout-specific fallback handling."""
    handler = FallbackHandler()
    initialize_default_strategies(handler)
    
    # Simulate a timeout error
    try:
        raise TimeoutError("Request timed out after 30 seconds")
    except Exception as e:
        fallback_response = handler.handle_failure(
            request="What is the meaning of life?",
            error=e
        )
    
    # Should use timeout recovery strategy
    assert isinstance(fallback_response, str)
    assert len(fallback_response) > 0
    # Check for timeout-related content
    assert any(word in fallback_response.lower() for word in ["longer", "think", "delay", "still"])
    
    print("✓ Timeout fallback test passed")


def test_connection_fallback():
    """Test connection-specific fallback handling."""
    handler = FallbackHandler()
    initialize_default_strategies(handler)
    
    # Simulate a connection error
    try:
        raise ConnectionError("Failed to connect to service")
    except Exception as e:
        fallback_response = handler.handle_failure(
            request="Generate a story",
            error=e
        )
    
    # Should use connection recovery strategy
    assert isinstance(fallback_response, str)
    assert len(fallback_response) > 0
    # Check for connection-related content
    assert any(word in fallback_response.lower() for word in ["connect", "connection", "network"])

    print("✓ Connection fallback test passed")


def test_default_strategies_initialization():
    """Test initialization of default fallback strategies."""
    handler = FallbackHandler()
    
    # Initialize default strategies
    assert initialize_default_strategies(handler) == True
    
    # Check that strategies were added
    assert "timeout_recovery" in handler.strategies
    assert "connection_recovery" in handler.strategies
    assert "content_safety" in handler.strategies
    assert "general_fallback" in handler.strategies
    
    # Check strategy properties
    timeout_strategy = handler.strategies["timeout_recovery"]
    assert timeout_strategy.priority == 10
    assert "timeout" in timeout_strategy.conditions
    assert len(timeout_strategy.static_responses) > 0
    
    print("✓ Default strategies initialization test passed")


def test_fallback_statistics():
    """Test fallback statistics tracking."""
    handler = FallbackHandler()
    initialize_default_strategies(handler)
    
    # Generate some fallback events
    try:
        raise TimeoutError("Timeout test")
    except Exception as e:
        handler.handle_failure("Test request 1", e)
    
    try:
        raise ConnectionError("Connection test")
    except Exception as e:
        handler.handle_failure("Test request 2", e)
    
    # Get statistics
    stats = handler.get_fallback_statistics()
    
    # Check statistics
    assert "total_fallbacks" in stats
    assert stats["total_fallbacks"] >= 2
    assert "strategies_used" in stats
    assert "errors_handled" in stats
    assert len(stats["errors_handled"]) >= 2
    
    print("✓ Fallback statistics test passed")


def test_retry_counting():
    """Test retry counting functionality."""
    handler = FallbackHandler()
    initialize_default_strategies(handler)
    
    request = "Test retry request"
    
    # Simulate multiple failures for same request
    for i in range(3):
        try:
            raise TimeoutError(f"Timeout attempt {i+1}")
        except Exception as e:
            fallback_response = handler.handle_failure(request, e)
    
    # Check retry count
    assert request in handler.retry_counts
    assert handler.retry_counts[request] == 3
    
    # Clear retry count
    handler.clear_retry_counts(request)
    assert request not in handler.retry_counts
    
    print("✓ Retry counting test passed")


def test_recent_events():
    """Test recent events tracking."""
    handler = FallbackHandler()
    initialize_default_strategies(handler)
    
    # Generate a fallback event
    try:
        raise ValueError("Test error")
    except Exception as e:
        handler.handle_failure("Test event request", e)
    
    # Get recent events
    recent_events = handler.get_recent_events(limit=5)
    
    # Check events
    assert isinstance(recent_events, list)
    assert len(recent_events) >= 1
    event = recent_events[0]
    assert event.original_request == "Test event request"
    assert event.error_type == "ValueError"
    
    print("✓ Recent events test passed")


if __name__ == "__main__":
    # Run all tests
    test_strategy_registration()
    test_failure_handling()
    test_timeout_fallback()
    test_connection_fallback()
    test_default_strategies_initialization()
    test_fallback_statistics()
    test_retry_counting()
    test_recent_events()
    print("\n🎉 All fallback handler tests passed!")
