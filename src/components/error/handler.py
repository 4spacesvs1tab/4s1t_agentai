"""
Centralized error handling system for the 4S1T Agent AI framework.

Provides comprehensive error handling, context preservation, recovery mechanisms, and reporting.
"""
import asyncio
import logging
import traceback
from typing import Dict, List, Any, Optional, Callable, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid
import json
import os

from utils.logger import setup_logger
from components.events.event_bus import Event, get_event_bus, publish

logger = setup_logger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorType(Enum):
    """Types of errors."""
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NETWORK = "network"
    DATABASE = "database"
    CONFIGURATION = "configuration"
    BUSINESS_LOGIC = "business_logic"
    SYSTEM = "system"
    EXTERNAL_SERVICE = "external_service"
    UNKNOWN = "unknown"


@dataclass
class ErrorContext:
    """Context information for an error."""
    component: str
    operation: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    stack_trace: Optional[str] = None


@dataclass
class ErrorReport:
    """A comprehensive error report."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: ErrorType
    severity: ErrorSeverity
    message: str
    context: ErrorContext
    timestamp: datetime = field(default_factory=datetime.now)
    recovery_attempts: int = 0
    recovery_successful: bool = False
    related_errors: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


class RecoveryStrategy:
    """Base class for error recovery strategies."""
    
    def __init__(self, name: str, max_attempts: int = 3):
        self.name = name
        self.max_attempts = max_attempts
    
    async def recover(self, error_report: ErrorReport) -> bool:
        """
        Attempt to recover from an error.
        
        Args:
            error_report: Error report to recover from
            
        Returns:
            True if recovery was successful, False otherwise
        """
        raise NotImplementedError("Subclasses must implement recover method")


class RetryRecoveryStrategy(RecoveryStrategy):
    """Recovery strategy that retries the operation."""
    
    def __init__(
        self, 
        name: str, 
        max_attempts: int = 3, 
        delay: float = 1.0,
        backoff_factor: float = 2.0
    ):
        super().__init__(name, max_attempts)
        self.delay = delay
        self.backoff_factor = backoff_factor
        self._operations: Dict[str, Callable[[], Any]] = {}
    
    def register_operation(self, operation_id: str, operation: Callable[[], Any]) -> None:
        """
        Register an operation that can be retried.
        
        Args:
            operation_id: Unique identifier for the operation
            operation: Operation function to retry
        """
        self._operations[operation_id] = operation
    
    async def recover(self, error_report: ErrorReport) -> bool:
        """
        Attempt to recover by retrying the operation.
        
        Args:
            error_report: Error report to recover from
            
        Returns:
            True if recovery was successful, False otherwise
        """
        operation_id = error_report.context.metadata.get("operation_id")
        if not operation_id or operation_id not in self._operations:
            logger.warning(f"No retryable operation found for error {error_report.id}")
            return False
        
        operation = self._operations[operation_id]
        delay = self.delay
        
        for attempt in range(self.max_attempts):
            try:
                if asyncio.iscoroutinefunction(operation):
                    await operation()
                else:
                    operation()
                logger.info(f"Retry recovery successful for error {error_report.id} on attempt {attempt + 1}")
                return True
            except Exception as e:
                logger.warning(f"Retry attempt {attempt + 1} failed for error {error_report.id}: {e}")
                if attempt < self.max_attempts - 1:
                    # Wait before next attempt with exponential backoff
                    await asyncio.sleep(delay)
                    delay *= self.backoff_factor
        
        logger.error(f"Retry recovery failed for error {error_report.id} after {self.max_attempts} attempts")
        return False


class ErrorHandler:
    """Centralized error handler with context preservation and recovery mechanisms."""
    
    _instance: Optional['ErrorHandler'] = None
    
    def __init__(self):
        """Initialize the error handler."""
        if ErrorHandler._instance is not None:
            raise RuntimeError("Use ErrorHandler.get_instance() to get the singleton instance")
            
        self._error_reports: Dict[str, ErrorReport] = {}
        self._error_listeners: List[Callable[[ErrorReport], Any]] = []
        self._recovery_strategies: Dict[ErrorType, RecoveryStrategy] = {}
        self._retry_strategies: Dict[str, RetryRecoveryStrategy] = {}
        self._suppress_errors: bool = False
        self._max_error_reports: int = 1000
        self._log_to_file: bool = True
        self._log_directory: str = "logs"
        self._bus = get_event_bus()
        
        # Create log directory if it doesn't exist
        if self._log_to_file and not os.path.exists(self._log_directory):
            os.makedirs(self._log_directory)
        
        ErrorHandler._instance = self
    
    @classmethod
    def get_instance(cls) -> 'ErrorHandler':
        """
        Get the singleton instance of the error handler.
        
        Returns:
            ErrorHandler instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def handle_exception(
        self, 
        exception: Exception, 
        error_type: ErrorType = ErrorType.UNKNOWN,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[ErrorContext] = None,
        tags: Optional[List[str]] = None
    ) -> ErrorReport:
        """
        Handle an exception with comprehensive error reporting.
        
        Args:
            exception: Exception to handle
            error_type: Type of error
            severity: Severity level
            context: Error context
            tags: Additional tags
            
        Returns:
            Error report
        """
        try:
            # Create error context if not provided
            if context is None:
                context = ErrorContext(
                    component="unknown",
                    operation="unknown"
                )
            
            # Capture stack trace
            stack_trace = traceback.format_exc()
            context.stack_trace = stack_trace
            
            # Determine error type from exception if not specified
            if error_type == ErrorType.UNKNOWN:
                error_type = self._infer_error_type(exception)
            
            # Determine severity from exception if not specified
            if severity == ErrorSeverity.MEDIUM:
                severity = self._infer_severity(exception)
            
            # Create error report
            error_report = ErrorReport(
                type=error_type,
                severity=severity,
                message=str(exception),
                context=context,
                tags=tags or []
            )
            
            # Store error report
            self._store_error_report(error_report)
            
            # Log error
            self._log_error(error_report)
            
            # Publish error event
            self._publish_error_event(error_report)
            
            # Notify listeners
            self._notify_listeners(error_report)
            
            # Attempt recovery if applicable
            self._attempt_recovery(error_report)
            
            return error_report
            
        except Exception as e:
            logger.error(f"Failed to handle exception: {e}")
            # Create minimal error report for the error handler failure
            minimal_context = ErrorContext(
                component="error_handler",
                operation="handle_exception"
            )
            minimal_report = ErrorReport(
                type=ErrorType.SYSTEM,
                severity=ErrorSeverity.CRITICAL,
                message=f"Error handler failed: {str(e)}",
                context=minimal_context
            )
            return minimal_report
    
    def _infer_error_type(self, exception: Exception) -> ErrorType:
        """
        Infer error type from exception.
        
        Args:
            exception: Exception to analyze
            
        Returns:
            Inferred error type
        """
        exception_type = type(exception).__name__
        
        # Map common exception types to error types
        type_mapping = {
            "ValueError": ErrorType.VALIDATION,
            "TypeError": ErrorType.VALIDATION,
            "ValidationError": ErrorType.VALIDATION,
            "AuthenticationError": ErrorType.AUTHENTICATION,
            "AuthorizationError": ErrorType.AUTHORIZATION,
            "ConnectionError": ErrorType.NETWORK,
            "TimeoutError": ErrorType.NETWORK,
            "DatabaseError": ErrorType.DATABASE,
            "OperationalError": ErrorType.DATABASE,
            "ConfigurationError": ErrorType.CONFIGURATION,
            "BusinessLogicError": ErrorType.BUSINESS_LOGIC
        }
        
        return type_mapping.get(exception_type, ErrorType.UNKNOWN)
    
    def _infer_severity(self, exception: Exception) -> ErrorSeverity:
        """
        Infer severity from exception.
        
        Args:
            exception: Exception to analyze
            
        Returns:
            Inferred severity
        """
        exception_type = type(exception).__name__
        
        # Critical exceptions
        critical_exceptions = [
            "SystemExit", "KeyboardInterrupt", "MemoryError", "RecursionError"
        ]
        
        # High severity exceptions
        high_exceptions = [
            "RuntimeError", "NotImplementedError", "AssertionError"
        ]
        
        if exception_type in critical_exceptions:
            return ErrorSeverity.CRITICAL
        elif exception_type in high_exceptions:
            return ErrorSeverity.HIGH
        else:
            return ErrorSeverity.MEDIUM
    
    def _store_error_report(self, error_report: ErrorReport) -> None:
        """
        Store an error report.
        
        Args:
            error_report: Error report to store
        """
        # Limit number of stored reports
        if len(self._error_reports) >= self._max_error_reports:
            # Remove oldest report
            oldest_id = next(iter(self._error_reports))
            del self._error_reports[oldest_id]
        
        self._error_reports[error_report.id] = error_report
        logger.debug(f"Stored error report {error_report.id}")
    
    def _log_error(self, error_report: ErrorReport) -> None:
        """
        Log an error report.
        
        Args:
            error_report: Error report to log
        """
        log_message = (
            f"Error [{error_report.type.value}] [{error_report.severity.value}]: "
            f"{error_report.message} "
            f"(Component: {error_report.context.component}, "
            f"Operation: {error_report.context.operation})"
        )
        
        # Log to console
        if error_report.severity == ErrorSeverity.CRITICAL:
            logger.critical(log_message)
        elif error_report.severity == ErrorSeverity.HIGH:
            logger.error(log_message)
        elif error_report.severity == ErrorSeverity.MEDIUM:
            logger.warning(log_message)
        else:
            logger.info(log_message)
        
        # Log to file if enabled
        if self._log_to_file:
            self._log_error_to_file(error_report)
    
    def _log_error_to_file(self, error_report: ErrorReport) -> None:
        """
        Log an error report to a file.
        
        Args:
            error_report: Error report to log
        """
        try:
            log_filename = f"error_{datetime.now().strftime('%Y-%m-%d')}.log"
            log_filepath = os.path.join(self._log_directory, log_filename)
            
            log_entry = {
                "timestamp": error_report.timestamp.isoformat(),
                "error_id": error_report.id,
                "type": error_report.type.value,
                "severity": error_report.severity.value,
                "message": error_report.message,
                "component": error_report.context.component,
                "operation": error_report.context.operation,
                "user_id": error_report.context.user_id,
                "session_id": error_report.context.session_id,
                "request_id": error_report.context.request_id,
                "tags": error_report.tags,
                "stack_trace": error_report.context.stack_trace
            }
            
            with open(log_filepath, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
                
        except Exception as e:
            logger.error(f"Failed to log error to file: {e}")
    
    def _publish_error_event(self, error_report: ErrorReport) -> None:
        """
        Publish an error event.
        
        Args:
            error_report: Error report to publish
        """
        try:
            event = Event(
                event_type=f"error.{error_report.type.value}",
                payload={
                    "error_id": error_report.id,
                    "error_type": error_report.type.value,
                    "severity": error_report.severity.value,
                    "message": error_report.message,
                    "component": error_report.context.component,
                    "operation": error_report.context.operation,
                    "timestamp": error_report.timestamp.isoformat(),
                    "tags": error_report.tags
                },
                source="error_handler",
                priority=self._severity_to_priority(error_report.severity)
            )
            asyncio.create_task(publish(event))
        except Exception as e:
            logger.error(f"Failed to publish error event: {e}")
    
    def _severity_to_priority(self, severity: ErrorSeverity) -> Any:
        """Convert error severity to event priority."""
        from components.events.event_bus import EventPriority
        priority_mapping = {
            ErrorSeverity.LOW: EventPriority.LOW,
            ErrorSeverity.MEDIUM: EventPriority.NORMAL,
            ErrorSeverity.HIGH: EventPriority.HIGH,
            ErrorSeverity.CRITICAL: EventPriority.CRITICAL
        }
        return priority_mapping.get(severity, EventPriority.NORMAL)
    
    def _notify_listeners(self, error_report: ErrorReport) -> None:
        """
        Notify error listeners.
        
        Args:
            error_report: Error report to notify about
        """
        for listener in self._error_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    asyncio.create_task(listener(error_report))
                else:
                    listener(error_report)
            except Exception as e:
                logger.error(f"Error in error listener: {e}")
    
    def _attempt_recovery(self, error_report: ErrorReport) -> None:
        """
        Attempt to recover from an error.
        
        Args:
            error_report: Error report to attempt recovery for
        """
        try:
            # Try type-specific recovery strategy
            if error_report.type in self._recovery_strategies:
                strategy = self._recovery_strategies[error_report.type]
                recovery_successful = False
                
                # Try recovery strategy
                if asyncio.iscoroutinefunction(strategy.recover):
                    recovery_successful = asyncio.create_task(strategy.recover(error_report))
                else:
                    recovery_successful = strategy.recover(error_report)
                
                # Update error report
                error_report.recovery_attempts += 1
                error_report.recovery_successful = recovery_successful
                
                if recovery_successful:
                    logger.info(f"Recovery successful for error {error_report.id}")
                    return
            
            # Try retry strategy if applicable
            retry_strategy_id = error_report.context.metadata.get("retry_strategy")
            if retry_strategy_id and retry_strategy_id in self._retry_strategies:
                retry_strategy = self._retry_strategies[retry_strategy_id]
                if asyncio.iscoroutinefunction(retry_strategy.recover):
                    recovery_successful = asyncio.create_task(retry_strategy.recover(error_report))
                else:
                    recovery_successful = retry_strategy.recover(error_report)
                
                # Update error report
                error_report.recovery_attempts += 1
                error_report.recovery_successful = recovery_successful
                
                if recovery_successful:
                    logger.info(f"Retry recovery successful for error {error_report.id}")
                    return
            
            logger.warning(f"No recovery strategy found for error {error_report.id}")
                    
        except Exception as e:
            logger.error(f"Error during recovery attempt for {error_report.id}: {e}")
    
    def add_error_listener(self, listener: Callable[[ErrorReport], Any]) -> None:
        """
        Add an error listener.
        
        Args:
            listener: Listener function to add
        """
        self._error_listeners.append(listener)
        logger.debug("Added error listener")
    
    def remove_error_listener(self, listener: Callable[[ErrorReport], Any]) -> bool:
        """
        Remove an error listener.
        
        Args:
            listener: Listener function to remove
            
        Returns:
            True if listener was removed, False if not found
        """
        try:
            self._error_listeners.remove(listener)
            logger.debug("Removed error listener")
            return True
        except ValueError:
            return False
    
    def register_recovery_strategy(
        self, 
        error_type: ErrorType, 
        strategy: RecoveryStrategy
    ) -> None:
        """
        Register a recovery strategy for an error type.
        
        Args:
            error_type: Error type
            strategy: Recovery strategy
        """
        self._recovery_strategies[error_type] = strategy
        logger.info(f"Registered recovery strategy for {error_type.value}")
    
    def register_retry_strategy(
        self, 
        strategy_id: str, 
        strategy: RetryRecoveryStrategy
    ) -> None:
        """
        Register a retry strategy.
        
        Args:
            strategy_id: Strategy identifier
            strategy: Retry strategy
        """
        self._retry_strategies[strategy_id] = strategy
        logger.info(f"Registered retry strategy {strategy_id}")
    
    def get_error_report(self, error_id: str) -> Optional[ErrorReport]:
        """
        Get an error report by ID.
        
        Args:
            error_id: Error report ID
            
        Returns:
            Error report or None if not found
        """
        return self._error_reports.get(error_id)
    
    def get_error_reports(
        self, 
        error_type: Optional[ErrorType] = None,
        severity: Optional[ErrorSeverity] = None,
        limit: int = 100
    ) -> List[ErrorReport]:
        """
        Get error reports with optional filtering.
        
        Args:
            error_type: Filter by error type
            severity: Filter by severity
            limit: Maximum number of reports to return
            
        Returns:
            List of error reports
        """
        reports = list(self._error_reports.values())
        
        # Apply filters
        if error_type:
            reports = [r for r in reports if r.type == error_type]
        if severity:
            reports = [r for r in reports if r.severity == severity]
        
        # Sort by timestamp (newest first)
        reports.sort(key=lambda r: r.timestamp, reverse=True)
        
        return reports[:limit]
    
    def clear_error_reports(self) -> None:
        """Clear all stored error reports."""
        self._error_reports.clear()
        logger.info("Cleared all error reports")
    
    def suppress_errors(self, suppress: bool = True) -> None:
        """
        Suppress error handling (for testing).
        
        Args:
            suppress: Whether to suppress errors
        """
        self._suppress_errors = suppress
        logger.info(f"Error suppression set to {suppress}")
    
    def enable_file_logging(self, enable: bool = True, directory: str = "logs") -> None:
        """
        Enable or disable file logging.
        
        Args:
            enable: Whether to enable file logging
            directory: Log directory
        """
        self._log_to_file = enable
        self._log_directory = directory
        
        # Create log directory if it doesn't exist
        if enable and not os.path.exists(directory):
            os.makedirs(directory)
        
        logger.info(f"File logging set to {enable} in directory {directory}")
    
    def create_context(
        self,
        component: str,
        operation: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ErrorContext:
        """
        Create an error context.
        
        Args:
            component: Component name
            operation: Operation name
            user_id: User ID
            session_id: Session ID
            request_id: Request ID
            metadata: Additional metadata
            
        Returns:
            Error context
        """
        return ErrorContext(
            component=component,
            operation=operation,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            metadata=metadata or {}
        )
    
    def generate_error_report_summary(self) -> Dict[str, Any]:
        """
        Generate a summary of all error reports.
        
        Returns:
            Error report summary
        """
        total_errors = len(self._error_reports)
        error_types = {}
        severities = {}
        components = {}
        
        for report in self._error_reports.values():
            # Count error types
            error_type = report.type.value
            error_types[error_type] = error_types.get(error_type, 0) + 1
            
            # Count severities
            severity = report.severity.value
            severities[severity] = severities.get(severity, 0) + 1
            
            # Count components
            component = report.context.component
            components[component] = components.get(component, 0) + 1
        
        return {
            "total_errors": total_errors,
            "error_types": error_types,
            "severities": severities,
            "components": components,
            "generated_at": datetime.now().isoformat()
        }


# Context manager for error handling
class ErrorContextManager:
    """Context manager for handling errors in a specific context."""
    
    def __init__(
        self, 
        component: str,
        operation: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None
    ):
        self.component = component
        self.operation = operation
        self.user_id = user_id
        self.session_id = session_id
        self.request_id = request_id
        self.error_handler = ErrorHandler.get_instance()
    
    def __enter__(self):
        """Enter the context."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context, handling any exceptions."""
        if exc_val is not None:
            context = self.error_handler.create_context(
                component=self.component,
                operation=self.operation,
                user_id=self.user_id,
                session_id=self.session_id,
                request_id=self.request_id
            )
            self.error_handler.handle_exception(exc_val, context=context)
            return True  # Suppress the exception
        return False


# Decorator for automatic error handling
def handle_errors(
    component: str,
    operation: str,
    error_type: ErrorType = ErrorType.UNKNOWN,
    severity: ErrorSeverity = ErrorSeverity.MEDIUM
):
    """
    Decorator for automatic error handling.
    
    Args:
        component: Component name
        operation: Operation name
        error_type: Default error type
        severity: Default severity
    """
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            error_handler = ErrorHandler.get_instance()
            context = error_handler.create_context(
                component=component,
                operation=operation
            )
            
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                error_handler.handle_exception(
                    e, 
                    error_type=error_type,
                    severity=severity,
                    context=context
                )
                raise  # Re-raise the exception
        
        def sync_wrapper(*args, **kwargs):
            error_handler = ErrorHandler.get_instance()
            context = error_handler.create_context(
                component=component,
                operation=operation
            )
            
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_handler.handle_exception(
                    e, 
                    error_type=error_type,
                    severity=severity,
                    context=context
                )
                raise  # Re-raise the exception
        
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# Convenience functions
def get_error_handler() -> ErrorHandler:
    """Get the global error handler instance."""
    return ErrorHandler.get_instance()


def handle_exception(
    exception: Exception,
    error_type: ErrorType = ErrorType.UNKNOWN,
    severity: ErrorSeverity = ErrorSeverity.MEDIUM,
    context: Optional[ErrorContext] = None,
    tags: Optional[List[str]] = None
) -> ErrorReport:
    """Handle an exception globally."""
    handler = get_error_handler()
    return handler.handle_exception(exception, error_type, severity, context, tags)


def create_error_context(
    component: str,
    operation: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> ErrorContext:
    """Create an error context globally."""
    handler = get_error_handler()
    return handler.create_context(
        component, operation, user_id, session_id, request_id, metadata
    )


def register_recovery_strategy(error_type: ErrorType, strategy: RecoveryStrategy) -> None:
    """Register a recovery strategy globally."""
    handler = get_error_handler()
    handler.register_recovery_strategy(error_type, strategy)


def register_retry_strategy(strategy_id: str, strategy: RetryRecoveryStrategy) -> None:
    """Register a retry strategy globally."""
    handler = get_error_handler()
    handler.register_retry_strategy(strategy_id, strategy)
