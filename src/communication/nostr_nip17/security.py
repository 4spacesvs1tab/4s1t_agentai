"""
Security utilities for Nostr NIP-17 client

Provides security features:
- Relay URL validation (block public relays when required)
- Rate limiting for message sending
- Security audit logging
- Input validation
"""
import time
import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps

from utils.logger import setup_logger
logger = setup_logger(__name__)


class SecurityLevel(Enum):
    """Security level enum."""
    BASIC = "basic"
    STRICT = "strict"
    MAXIMUM = "maximum"


@dataclass
class SecurityConfig:
    """Security configuration."""
    enforce_local_relay_only: bool = True
    allowed_local_relays: List[str] = field(default_factory=lambda: [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "172.20.0.1"  # Docker bridge — strfry relay
    ])
    max_message_size: int = 10000  # 10KB
    rate_limit_messages_per_minute: int = 10
    audit_logging: bool = True
    security_level: SecurityLevel = SecurityLevel.STRICT


class SecurityValidator:
    """
    Validates security-related inputs and operations.
    """
    
    def __init__(self, config: Optional[SecurityConfig] = None):
        self.config = config or SecurityConfig()
        self._message_timestamps: List[float] = []
        self._security_events: List[Dict] = []
    
    def validate_relay_url(self, relay_url: str) -> Tuple[bool, str]:
        """
        Validate relay URL against security policy.
        
        Args:
            relay_url: The relay URL to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not relay_url:
            return False, "Relay URL is empty"
        
        url_lower = relay_url.lower().strip()
        
        # Check for local relay
        if self.config.enforce_local_relay_only:
            is_local = any(
                local_host in url_lower 
                for local_host in self.config.allowed_local_relays
            )
            
            if not is_local:
                error_msg = f"Public relay not allowed: {relay_url}. " \
                           f"Only local relays are permitted per security requirements."
                self._log_security_event("RELAY_BLOCKED", relay_url, error_msg)
                logger.warning(error_msg)
                return False, error_msg
        
        # Validate URL format
        if not (url_lower.startswith('ws://') or url_lower.startswith('wss://')):
            return False, "Relay URL must start with ws:// or wss://"
        
        return True, ""
    
    def validate_message_size(self, message: str) -> Tuple[bool, str]:
        """Validate message size."""
        if len(message) > self.config.max_message_size:
            error_msg = f"Message too large: {len(message)} bytes (max {self.config.max_message_size})"
            self._log_security_event("MESSAGE_SIZE_EXCEEDED", None, error_msg)
            return False, error_msg
        return True, ""
    
    def validate_input(self, input_str: str) -> Tuple[bool, str]:
        """Validate input string for injection attacks."""
        if not input_str:
            return False, "Input is empty"
        
        # Check for common injection patterns
        injection_patterns = [
            r'<script',
            r'javascript:',
            r'on\w+\s*=',  # Event handlers
            r'\$\(',       # Command injection
            r'`',          # Backticks (template literals)
        ]
        
        for pattern in injection_patterns:
            if re.search(pattern, input_str, re.IGNORECASE):
                error_msg = f"Potentially malicious input detected"
                self._log_security_event("INPUT_VALIDATION_FAILED", None, error_msg)
                return False, error_msg
        
        return True, ""
    
    def check_rate_limit(self) -> Tuple[bool, str]:
        """
        Check if rate limit allows new message.
        
        Returns:
            Tuple of (allowed, error_message)
        """
        current_time = time.time()
        window_start = current_time - 60  # 1 minute window
        
        # Remove timestamps outside the window
        self._message_timestamps = [
            ts for ts in self._message_timestamps 
            if ts > window_start
        ]
        
        if len(self._message_timestamps) >= self.config.rate_limit_messages_per_minute:
            error_msg = f"Rate limit exceeded: {len(self._message_timestamps)} messages in last minute"
            self._log_security_event("RATE_LIMIT_EXCEEDED", None, error_msg)
            return False, error_msg
        
        return True, ""
    
    def record_message_sent(self) -> None:
        """Record a message send event for rate limiting."""
        self._message_timestamps.append(time.time())
        if self.config.audit_logging:
            self._log_security_event("MESSAGE_SENT", None, "Message sent successfully")
    
    def _log_security_event(self, event_type: str, details: Optional[str], description: str) -> None:
        """Log a security event."""
        event = {
            'timestamp': time.time(),
            'event_type': event_type,
            'details': details,
            'description': description
        }
        self._security_events.append(event)
        logger.info(f"Security Event [{event_type}]: {description}")


class RateLimiter:
    """
    Rate limiter decorator for functions.
    """
    
    def __init__(self, max_calls: int, period: int = 60):
        self.max_calls = max_calls
        self.period = period
        self._calls: List[float] = []
    
    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_time = time.time()
            window_start = current_time - self.period
            
            # Remove old calls
            self._calls = [ts for ts in self._calls if ts > window_start]
            
            if len(self._calls) >= self.max_calls:
                raise RuntimeError(f"Rate limit exceeded: {self.max_calls} calls per {self.period} seconds")
            
            self._calls.append(current_time)
            return func(*args, **kwargs)
        
        return wrapper


def create_security_validator(config: Optional[SecurityConfig] = None) -> SecurityValidator:
    """Factory function for security validator."""
    return SecurityValidator(config)
