"""
Message validation system for the 4S1T Agent AI framework.

Provides validation for messages to ensure data integrity and security.
"""
import re
import logging
from typing import Dict, Any, List, Optional, Callable, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json

from utils.logger import setup_logger
from components.messaging.queue import Message, MessageType

logger = setup_logger(__name__)


class ValidationError(Exception):
    """Exception raised for message validation errors."""
    pass


@dataclass
class ValidationRule:
    """A validation rule for message fields."""
    field: str
    validator: Callable[[Any], bool]
    error_message: str
    required: bool = True


class MessageValidator:
    """Validates messages according to defined rules."""
    
    def __init__(self):
        """Initialize the message validator."""
        self._rules: Dict[str, List[ValidationRule]] = {}
        self._default_rules: List[ValidationRule] = []
        self._setup_default_rules()
        logger.info("Message validator initialized")
    
    def _setup_default_rules(self):
        """Set up default validation rules."""
        # Message ID validation
        self._default_rules.append(
            ValidationRule(
                field="id",
                validator=lambda x: isinstance(x, str) and len(x) > 0 and len(x) <= 128,
                error_message="Message ID must be a string between 1 and 128 characters"
            )
        )
        
        # Message type validation
        valid_types = [t.value for t in MessageType]
        self._default_rules.append(
            ValidationRule(
                field="type",
                validator=lambda x: isinstance(x, MessageType) or (isinstance(x, str) and x in valid_types),
                error_message=f"Message type must be one of: {valid_types}"
            )
        )
        
        # Source validation
        self._default_rules.append(
            ValidationRule(
                field="source",
                validator=lambda x: isinstance(x, str) and len(x) > 0 and len(x) <= 128,
                error_message="Source must be a string between 1 and 128 characters"
            )
        )
        
        # Destination validation (optional)
        self._default_rules.append(
            ValidationRule(
                field="destination",
                validator=lambda x: x is None or (isinstance(x, str) and len(x) <= 128),
                error_message="Destination must be None or a string up to 128 characters",
                required=False
            )
        )
        
        # Topic validation
        self._default_rules.append(
            ValidationRule(
                field="topic",
                validator=lambda x: isinstance(x, str) and len(x) <= 128 and re.match(r'^[a-zA-Z0-9_.-]*$', x),
                error_message="Topic must be a string up to 128 characters containing only alphanumeric characters, dots, underscores, and hyphens",
                required=False
            )
        )
        
        # Timestamp validation
        self._default_rules.append(
            ValidationRule(
                field="timestamp",
                validator=lambda x: isinstance(x, datetime),
                error_message="Timestamp must be a datetime object"
            )
        )
        
        # Priority validation
        self._default_rules.append(
            ValidationRule(
                field="priority",
                validator=lambda x: isinstance(x, int) and x >= 0,
                error_message="Priority must be a non-negative integer"
            )
        )
        
        # TTL validation
        self._default_rules.append(
            ValidationRule(
                field="ttl",
                validator=lambda x: x is None or (isinstance(x, int) and x > 0),
                error_message="TTL must be None or a positive integer",
                required=False
            )
        )
    
    def add_validation_rule(self, message_type: str, rule: ValidationRule) -> None:
        """
        Add a validation rule for a specific message type.
        
        Args:
            message_type: Message type to apply rule to
            rule: Validation rule to add
        """
        if message_type not in self._rules:
            self._rules[message_type] = []
        self._rules[message_type].append(rule)
        logger.debug(f"Added validation rule for message type '{message_type}': {rule.field}")
    
    def add_validation_rules(self, message_type: str, rules: List[ValidationRule]) -> None:
        """
        Add multiple validation rules for a specific message type.
        
        Args:
            message_type: Message type to apply rules to
            rules: List of validation rules to add
        """
        for rule in rules:
            self.add_validation_rule(message_type, rule)
    
    def validate_message(self, message: Message) -> bool:
        """
        Validate a message according to defined rules.
        
        Args:
            message: Message to validate
            
        Returns:
            True if message is valid, False otherwise
            
        Raises:
            ValidationError: If message fails validation
        """
        try:
            # Apply default rules
            for rule in self._default_rules:
                self._validate_field(message, rule)
            
            # Apply type-specific rules
            if message.type.value in self._rules:
                for rule in self._rules[message.type.value]:
                    self._validate_field(message, rule)
            
            # Apply topic-specific rules
            if message.topic and message.topic in self._rules:
                for rule in self._rules[message.topic]:
                    self._validate_field(message, rule)
            
            logger.debug(f"Message {message.id} validated successfully")
            return True
            
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error during message validation: {e}")
            raise ValidationError(f"Validation failed: {str(e)}")
    
    def _validate_field(self, message: Message, rule: ValidationRule) -> None:
        """
        Validate a specific field according to a rule.
        
        Args:
            message: Message to validate
            rule: Validation rule to apply
            
        Raises:
            ValidationError: If field fails validation
        """
        # Get field value
        if hasattr(message, rule.field):
            value = getattr(message, rule.field)
        elif rule.field in message.payload:
            value = message.payload[rule.field]
        elif rule.field in message.metadata:
            value = message.metadata[rule.field]
        else:
            value = None
        
        # Check if required field is missing
        if rule.required and value is None:
            raise ValidationError(f"Required field '{rule.field}' is missing")
        
        # Skip validation for None values of optional fields
        if not rule.required and value is None:
            return
        
        # Apply validator
        if not rule.validator(value):
            raise ValidationError(f"Field '{rule.field}' failed validation: {rule.error_message}")
    
    def sanitize_message(self, message: Message) -> Message:
        """
        Sanitize a message by removing potentially dangerous content.
        
        Args:
            message: Message to sanitize
            
        Returns:
            Sanitized message
        """
        try:
            # Create a copy of the message
            sanitized_payload = {}
            if message.payload:
                sanitized_payload = self._sanitize_dict(message.payload)
            
            sanitized_metadata = {}
            if message.metadata:
                sanitized_metadata = self._sanitize_dict(message.metadata)
            
            sanitized_message = Message(
                id=message.id,
                type=message.type,
                source=message.source,
                destination=message.destination,
                topic=message.topic,
                payload=sanitized_payload,
                timestamp=message.timestamp,
                correlation_id=message.correlation_id,
                reply_to=message.reply_to,
                priority=message.priority,
                ttl=message.ttl,
                metadata=sanitized_metadata
            )
            
            logger.debug(f"Message {message.id} sanitized successfully")
            return sanitized_message
            
        except Exception as e:
            logger.error(f"Failed to sanitize message {message.id}: {e}")
            raise ValidationError(f"Sanitization failed: {str(e)}")
    
    def _sanitize_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize a dictionary by removing potentially dangerous content.
        
        Args:
            data: Dictionary to sanitize
            
        Returns:
            Sanitized dictionary
        """
        sanitized = {}
        for key, value in data.items():
            # Skip keys that look suspicious
            if self._is_suspicious_key(key):
                logger.warning(f"Skipping suspicious key: {key}")
                continue
            
            # Sanitize values
            sanitized_value = self._sanitize_value(value)
            sanitized[key] = sanitized_value
        
        return sanitized
    
    def _is_suspicious_key(self, key: str) -> bool:
        """
        Check if a key looks suspicious (potentially malicious).
        
        Args:
            key: Key to check
            
        Returns:
            True if key looks suspicious, False otherwise
        """
        suspicious_patterns = [
            r'password',
            r'secret',
            r'token',
            r'key',
            r'credential',
            r'auth',
            r'cookie',
            r'session'
        ]
        
        key_lower = key.lower()
        return any(re.search(pattern, key_lower) for pattern in suspicious_patterns)
    
    def _sanitize_value(self, value: Any) -> Any:
        """
        Sanitize a value by removing potentially dangerous content.
        
        Args:
            value: Value to sanitize
            
        Returns:
            Sanitized value
        """
        if isinstance(value, str):
            # Remove null bytes and other potentially dangerous characters
            sanitized = value.replace('\x00', '').replace('\x01', '').replace('\x02', '')
            return sanitized
        elif isinstance(value, dict):
            return self._sanitize_dict(value)
        elif isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        else:
            return value
    
    def validate_json_schema(self, message: Message, schema: Dict[str, Any]) -> bool:
        """
        Validate message payload against a JSON schema.
        
        Args:
            message: Message to validate
            schema: JSON schema to validate against
            
        Returns:
            True if message payload matches schema, False otherwise
            
        Raises:
            ValidationError: If validation fails
        """
        try:
            # Import jsonschema only when needed
            import jsonschema
            
            # Validate payload against schema
            jsonschema.validate(instance=message.payload, schema=schema)
            logger.debug(f"Message {message.id} validated against JSON schema")
            return True
            
        except ImportError:
            logger.warning("jsonschema not available, skipping JSON schema validation")
            return True
        except jsonschema.ValidationError as e:
            raise ValidationError(f"JSON schema validation failed: {e.message}")
        except jsonschema.SchemaError as e:
            raise ValidationError(f"Invalid JSON schema: {e.message}")


# Predefined validators
def create_string_validator(min_length: int = 0, max_length: int = 1024, pattern: str = None) -> Callable[[str], bool]:
    """
    Create a string validator with length and pattern constraints.
    
    Args:
        min_length: Minimum string length
        max_length: Maximum string length
        pattern: Regular expression pattern (optional)
        
    Returns:
        Validator function
    """
    def validator(value: str) -> bool:
        if not isinstance(value, str):
            return False
        if len(value) < min_length or len(value) > max_length:
            return False
        if pattern and not re.match(pattern, value):
            return False
        return True
    return validator


def create_numeric_validator(min_value: Union[int, float] = None, max_value: Union[int, float] = None) -> Callable[[Union[int, float]], bool]:
    """
    Create a numeric validator with range constraints.
    
    Args:
        min_value: Minimum value (optional)
        max_value: Maximum value (optional)
        
    Returns:
        Validator function
    """
    def validator(value: Union[int, float]) -> bool:
        if not isinstance(value, (int, float)):
            return False
        if min_value is not None and value < min_value:
            return False
        if max_value is not None and value > max_value:
            return False
        return True
    return validator


def create_enum_validator(valid_values: List[Any]) -> Callable[[Any], bool]:
    """
    Create an enum validator that checks if value is in a list of valid values.
    
    Args:
        valid_values: List of valid values
        
    Returns:
        Validator function
    """
    def validator(value: Any) -> bool:
        return value in valid_values
    return validator


# Global message validator instance
message_validator: Optional[MessageValidator] = None


def get_message_validator() -> MessageValidator:
    """
    Get singleton message validator instance.
    
    Returns:
        MessageValidator instance
    """
    global message_validator
    if message_validator is None:
        message_validator = MessageValidator()
    return message_validator


def validate_message(message: Message) -> bool:
    """
    Validate a message globally.
    
    Args:
        message: Message to validate
        
    Returns:
        True if message is valid, False otherwise
    """
    validator = get_message_validator()
    return validator.validate_message(message)


def sanitize_message(message: Message) -> Message:
    """
    Sanitize a message globally.
    
    Args:
        message: Message to sanitize
        
    Returns:
        Sanitized message
    """
    validator = get_message_validator()
    return validator.sanitize_message(message)
