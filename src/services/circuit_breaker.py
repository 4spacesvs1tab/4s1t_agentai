"""
Circuit Breaker Pattern Implementation for 4S1T Agent AI

Provides resilience against external API failures by preventing cascading failures
and enabling graceful degradation when NanoGPT API is unavailable.

Priority: HIGH - Critical for service resilience
Implementation Date: 2026-01-15
Status: In Development
"""

import asyncio
import time
from typing import Any, Callable, Optional, Dict
from enum import Enum
from dataclasses import dataclass
from src.utils.logger import get_logger

# Initialize logger
logger = get_logger(__name__)


class CircuitState(Enum):
    """Circuit breaker states following the pattern."""
    CLOSED = "closed"  # Normal operation, calls pass through
    OPEN = "open"      # Circuit tripped, calls are rejected
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    failure_threshold: int = 3  # Failures before opening circuit
    recovery_timeout: int = 60  # Seconds before attempting recovery
    success_threshold: int = 2  # Successes needed to close circuit
    time_window: int = 120  # Seconds window for tracking (2 minutes)
    
    def __post_init__(self):
        """Validate configuration."""
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if self.recovery_timeout < 1:
            raise ValueError("recovery_timeout must be at least 1 second")
        if self.success_threshold < 1:
            raise ValueError("success_threshold must be at least 1")
        if self.time_window < self.recovery_timeout:
            raise ValueError("time_window must be >= recovery_timeout")


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open or call fails."""
    pass


class CircuitBreaker:
    """
    Circuit breaker for external API resilience.
    
    Provides protection against cascading failures by monitoring
    call success/failure rates and automatically tripping when
    failure threshold is exceeded.
    """
    
    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        """
        Initialize circuit breaker.
        
        Args:
            name: Name of the circuit (e.g., "NanoGPT_API")
            config: Circuit breaker configuration
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        
        # Failure tracking
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._last_success_time = 0.0
        self._last_operation_time = 0.0
        
        # Thread safety
        self._lock = asyncio.Lock()
        
        logger.info(f"Circuit breaker initialized: {self.name}")
        logger.debug(f"Config: {self.config}")
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with circuit breaker protection.
        
        Args:
            func: Async function to call
            *args: Positional arguments
            **kwargs: Keyword arguments
            
        Returns:
            Result of the function call
            
        Raises:
            CircuitBreakerError: If circuit is open or call fails
            Exception: Original exception if call fails and circuit isn't tripped
        """
        async with self._lock:
            now = time.time()
            
            # Check if we're outside the tracking window
            if now - self._last_operation_time > self.config.time_window:
                logger.debug(f"[{self.name}] Time window expired, resetting counters")
                self._reset_counters()
            
            self._last_operation_time = now
            
            # Handle OPEN state
            if self.state == CircuitState.OPEN:
                if now - self._last_failure_time > self.config.recovery_timeout:
                    # Transition to HALF_OPEN for recovery test
                    await self._transition_to_half_open()
                else:
                    # Still in recovery period, reject call
                    remaining = self.config.recovery_timeout - (now - self._last_failure_time)
                    logger.warning(
                        f"[{self.name}] Circuit breaker is OPEN "
                        f"({remaining:.1f}s remaining in recovery)"
                    )
                    raise CircuitBreakerError(
                        f"[{self.name}] Circuit breaker is OPEN. "
                        f"Estimated recovery in {remaining:.1f} seconds."
                    )
            
            # Attempt the call
            try:
                result = await func(*args, **kwargs)
                
                # Success handling
                await self._handle_success(now)
                
                return result
                
            except Exception as e:
                # Failure handling
                await self._handle_failure(e, now)
                
                # Re-raise with circuit breaker context
                if self.state == CircuitState.OPEN:
                    raise CircuitBreakerError(
                        f"[{self.name}] Call failed and circuit opened: {str(e)}"
                    ) from e
                else:
                    # In HALF_OPEN or CLOSED, let the original exception propagate
                    raise
    
    async def _handle_success(self, timestamp: float) -> None:
        """Handle successful call."""
        self._last_success_time = timestamp
        
        if self.state == CircuitState.HALF_OPEN:
            # In HALF_OPEN, track successes toward closing
            self._success_count += 1
            logger.info(
                f"[{self.name}] Success in HALF_OPEN state "
                f"({self._success_count}/{self.config.success_threshold})"
            )
            
            if self._success_count >= self.config.success_threshold:
                await self._transition_to_closed()
        else:
            # In CLOSED, reset failure count on success
            if self._failure_count > 0:
                logger.debug(f"[{self.name}] Success after failures, resetting failure count")
                self._failure_count = 0
    
    async def _handle_failure(self, error: Exception, timestamp: float) -> None:
        """Handle failed call."""
        self._last_failure_time = timestamp
        self._failure_count += 1
        self._success_count = 0  # Reset success streak
        
        logger.warning(
            f"[{self.name}] Call failed "
            f"({self._failure_count}/{self.config.failure_threshold}): {error}"
        )
        
        if self._failure_count >= self.config.failure_threshold:
            await self._transition_to_open()
    
    async def _transition_to_open(self) -> None:
        """Transition circuit to OPEN state."""
        old_state = self.state
        self.state = CircuitState.OPEN
        
        logger.error(
            f"[{self.name}] Circuit breaker OPENED "
            f"(threshold: {self.config.failure_threshold}, "
            f"recovery in: {self.config.recovery_timeout}s)"
        )
        
        # Log state transition for monitoring
        logger.info(
            f"[{self.name}] State transition: {old_state.value} → OPEN"
        )
    
    async def _transition_to_half_open(self) -> None:
        """Transition circuit to HALF_OPEN state."""
        old_state = self.state
        self.state = CircuitState.HALF_OPEN
        self._success_count = 0  # Reset success counter
        
        logger.info(
            f"[{self.name}] Circuit breaker entering HALF_OPEN for recovery test"
        )
        
        logger.info(
            f"[{self.name}] State transition: {old_state.value} → HALF_OPEN"
        )
    
    async def _transition_to_closed(self) -> None:
        """Transition circuit to CLOSED state."""
        old_state = self.state
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        
        logger.info(
            f"[{self.name}] Circuit breaker CLOSED "
            f"(service recovered successfully)"
        )
        
        logger.info(
            f"[{self.name}] State transition: {old_state.value} → CLOSED"
        )
    
    def _reset_counters(self) -> None:
        """Reset failure and success counters."""
        self._failure_count = 0
        self._success_count = 0
        logger.debug(f"[{self.name}] Counters reset due to time window expiration")
    
    def get_state(self) -> Dict[str, Any]:
        """
        Get current circuit breaker state for monitoring.
        
        Returns:
            Dictionary with circuit state information
        """
        now = time.time()
        
        # Calculate time since last failure (if any)
        time_since_failure = None
        if self._last_failure_time > 0:
            time_since_failure = now - self._last_failure_time
        
        # Calculate time until recovery (if circuit is open)
        time_until_recovery = None
        if self.state == CircuitState.OPEN and time_since_failure is not None:
            remaining = self.config.recovery_timeout - time_since_failure
            time_until_recovery = max(0, remaining)
        
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self.config.failure_threshold,
            "success_threshold": self.config.success_threshold,
            "recovery_timeout": self.config.recovery_timeout,
            "time_window": self.config.time_window,
            "last_failure_time": self._last_failure_time,
            "last_success_time": self._last_success_time,
            "time_since_failure": time_since_failure,
            "time_until_recovery": time_until_recovery,
            "is_closed": self.state == CircuitState.CLOSED,
            "is_open": self.state == CircuitState.OPEN,
            "is_half_open": self.state == CircuitState.HALF_OPEN
        }
    
    def __str__(self) -> str:
        """String representation for debugging."""
        state_info = self.get_state()
        return (
            f"CircuitBreaker({self.name}, "
            f"state={self.state.value}, "
            f"failures={self._failure_count}/{self.config.failure_threshold})"
        )
    
    def __repr__(self) -> str:
        """Detailed representation."""
        return self.__str__()
