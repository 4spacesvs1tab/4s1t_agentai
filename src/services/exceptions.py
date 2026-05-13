"""
Custom exception hierarchy for 4S1T Agent AI.
Provides defense-in-depth security with typed, safe error handling.
"""

class DatabaseError(Exception):
    """Base database exception - prevents information leakage."""
    def __init__(self, message="Database operation failed"):
        super().__init__(message)

class AuthError(DatabaseError):
    """Authentication errors - security-safe messages."""
    def __init__(self, message="Authentication failed"):
        super().__init__(message)

class ConfigurationError(Exception):
    """Configuration validation errors."""
    def __init__(self, message="Configuration validation failed"):
        super().__init__(message)

class ValidationError(ConfigurationError):
    """Input validation errors - safe error messages."""
    def __init__(self, message="Input validation failed"):
        super().__init__(message)

class ServiceError(Exception):
    """Service layer errors - hide implementation details."""
    def __init__(self, message="Service operation failed"):
        super().__init__(message)

class CircuitBreakerError(ServiceError):
    """Circuit breaker protection - resilience pattern."""
    def __init__(self, message="Circuit breaker activated - service protection"):
        super().__init__(message)

class ExternalAPIError(ServiceError):
    """External API failures - prevents cascade failures."""
    def __init__(self, message="External service temporarily unavailable"):
        super().__init__(message)

class AuthorizationError(ServiceError):
    """Authorization failures - security boundary."""
    def __init__(self, message="Authorization required"):
        super().__init__(message)


class PermissionError(ServiceError):
    """Permission denied errors for security violations."""
    def __init__(self, message="Permission denied"):
        super().__init__(message)

class MFAError(ServiceError):
    """Multi-factor authentication errors."""
    def __init__(self, message="MFA verification failed"):
        super().__init__(message)

class AccountLockedError(AuthError):
    """Account temporarily locked due to repeated authentication failures (P3-3)."""
    def __init__(self, locked_until: str, message: str = "Account temporarily locked"):
        super().__init__(message)
        self.locked_until = locked_until  # ISO-8601 UTC string

# Alias for consistency with security dependencies
AuthenticationError = AuthError
