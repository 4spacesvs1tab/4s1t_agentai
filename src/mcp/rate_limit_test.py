"""
Test to demonstrate rate limiting functionality.
"""

import sys
import os
import time

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.security import RateLimiter


def test_rate_limiting():
    """Test rate limiting with a smaller window for faster testing."""
    print("Testing Rate Limiting...")
    
    # Create rate limiter with 5 requests per minute for testing
    rate_limiter = RateLimiter(requests_per_minute=5)
    client_id = "fresh_test_client"  # Use a fresh client ID
    
    # Make 5 requests - all should be allowed
    print("Making 5 requests (should all be allowed):")
    for i in range(5):
        allowed = rate_limiter.is_allowed(client_id)
        remaining = rate_limiter.get_remaining_requests(client_id)
        print(f"  Request {i+1}: Allowed={allowed}, Remaining={remaining}")
    
    # Make 2 more requests - these should be denied
    print("\nMaking 2 more requests (should be denied):")
    for i in range(2):
        allowed = rate_limiter.is_allowed(client_id)
        remaining = rate_limiter.get_remaining_requests(client_id)
        print(f"  Request {i+6}: Allowed={allowed}, Remaining={remaining}")
    
    # Clear the client requests to simulate a new window
    if hasattr(rate_limiter, 'client_requests'):
        rate_limiter.client_requests.clear()
    
    # Make 5 more requests - all should be allowed again
    print("\nMaking 5 more requests after clearing window (should all be allowed):")
    for i in range(5):
        allowed = rate_limiter.is_allowed(client_id)
        remaining = rate_limiter.get_remaining_requests(client_id)
        print(f"  Request {i+1}: Allowed={allowed}, Remaining={remaining}")


if __name__ == "__main__":
    test_rate_limiting()
