"""
Simple in-memory sliding-window rate limiter for FastAPI endpoints.

Usage:
    from utils.rate_limit import rate_limit

    _login_limiter = rate_limit(max_calls=5, window_seconds=60)

    @router.post("/login")
    async def login(..., _rl=Depends(_login_limiter)):
        ...
"""
import time
import threading
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request, HTTPException, status


class _SlidingWindowLimiter:
    """Thread-safe per-key sliding-window rate limiter."""

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._lock = threading.Lock()
        self._calls: dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            q = self._calls[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_calls:
                return False
            q.append(now)
            return True


def rate_limit(max_calls: int, window_seconds: int) -> Callable:
    """
    Return a FastAPI dependency that rate-limits callers by client IP.

    Args:
        max_calls: Maximum requests allowed in the window.
        window_seconds: Length of the sliding window in seconds.

    Returns:
        FastAPI dependency callable.
    """
    limiter = _SlidingWindowLimiter(max_calls, window_seconds)

    async def dependency(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.is_allowed(client_ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many requests. Please wait {window_seconds} seconds before retrying.",
                headers={"Retry-After": str(window_seconds)},
            )

    return dependency
