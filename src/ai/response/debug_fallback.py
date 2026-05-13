"""
Debug fallback response issue.
"""

import sys
import os

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai.response.fallback import FallbackHandler, initialize_default_strategies


def debug_fallback_response():
    """Debug fallback response issue."""
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
    
    print(f"Fallback response: {fallback_response}")
    print(f"Response length: {len(fallback_response)}")
    print(f"'sorry' in response: {'sorry' in fallback_response.lower()}")
    print(f"'trouble' in response: {'trouble' in fallback_response.lower()}")
    
    # Check what strategies are available
    print(f"Available strategies: {list(handler.strategies.keys())}")
    
    # Check the specific timeout strategy
    if "timeout_recovery" in handler.strategies:
        strategy = handler.strategies["timeout_recovery"]
        print(f"Timeout strategy responses: {strategy.static_responses}")


if __name__ == "__main__":
    debug_fallback_response()
