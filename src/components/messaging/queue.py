"""
Message queue system for the 4S1T Agent AI framework.

Provides reliable message passing between components with persistence, serialization, and validation.
"""
import asyncio
import json
import pickle
import logging
from typing import Dict, List, Any, Optional, Callable, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid

from utils.logger import setup_logger
from components.events.event_bus import Event, get_event_bus, publish, subscribe
from components.messaging.validator import get_message_validator, validate_message, sanitize_message, ValidationError

logger = setup_logger(__name__)


class MessageType(Enum):
    """Types of messages that can be sent."""
    COMMAND = "command"
    RESPONSE = "response"
    EVENT = "event"
    REQUEST = "request"
    NOTIFICATION = "notification"
    ERROR = "error"


class SerializationFormat(Enum):
    """Supported serialization formats."""
    JSON = "json"
    PICKLE = "pickle"
    MSGPACK = "msgpack"


@dataclass
class Message:
    """A message that can be sent between components."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: MessageType = MessageType.NOTIFICATION
    source: str = ""
    destination: Optional[str] = None
    topic: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    correlation_id: Optional[str] = None
    reply_to: Optional[str] = None
    priority: int = 0
    ttl: Optional[int] = None  # Time to live in seconds
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate message after initialization."""
        if self.ttl is not None and self.ttl <= 0:
            raise ValueError("TTL must be positive if specified")
        if self.priority < 0:
            raise ValueError("Priority must be non-negative")


@dataclass
class QueueStats:
    """Statistics for a message queue."""
    queue_name: str
    message_count: int = 0
    processed_count: int = 0
    error_count: int = 0
    validation_error_count: int = 0
    last_processed: Optional[datetime] = None
    average_processing_time: float = 0.0


class MessageQueue:
    """A message queue for reliable inter-component communication."""
    
    def __init__(self, name: str, max_size: int = 1000):
        """
        Initialize a message queue.
        
        Args:
            name: Queue name
            max_size: Maximum number of messages in queue
        """
        self.name = name
        self.max_size = max_size
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._processing_task: Optional[asyncio.Task] = None
        self._running = False
        self._handlers: Dict[str, Callable[[Message], Any]] = {}
        self._stats = QueueStats(queue_name=name)
        self._serialization_format = SerializationFormat.JSON
        self._validator = get_message_validator()
        self._validate_incoming = True
        self._sanitize_incoming = True
        
        logger.info(f"Message queue '{name}' initialized with max size {max_size}")
    
    def set_serialization_format(self, format: SerializationFormat) -> None:
        """
        Set the serialization format for messages.
        
        Args:
            format: Serialization format
        """
        self._serialization_format = format
        logger.info(f"Serialization format set to {format.value}")
    
    def set_validation_options(self, validate: bool = True, sanitize: bool = True) -> None:
        """
        Set validation and sanitization options.
        
        Args:
            validate: Whether to validate incoming messages
            sanitize: Whether to sanitize incoming messages
        """
        self._validate_incoming = validate
        self._sanitize_incoming = sanitize
        logger.info(f"Validation set to {validate}, sanitization set to {sanitize}")
    
    async def send_message(self, message: Message) -> bool:
        """
        Send a message to the queue.
        
        Args:
            message: Message to send
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        try:
            # Validate outgoing message
            if not validate_message(message):
                logger.error(f"Outgoing message {message.id} failed validation")
                self._stats.validation_error_count += 1
                return False
            
            # Check TTL if specified
            if message.ttl is not None:
                expiry_time = message.timestamp.timestamp() + message.ttl
                if datetime.now().timestamp() > expiry_time:
                    logger.warning(f"Message {message.id} expired before sending")
                    self._stats.error_count += 1
                    return False
            
            # Add to queue
            await self._queue.put(message)
            self._stats.message_count += 1
            
            logger.debug(f"Message {message.id} sent to queue '{self.name}'")
            return True
        except asyncio.QueueFull:
            logger.error(f"Queue '{self.name}' is full, message {message.id} dropped")
            self._stats.error_count += 1
            return False
        except ValidationError as e:
            logger.error(f"Message {message.id} failed validation: {e}")
            self._stats.validation_error_count += 1
            return False
        except Exception as e:
            logger.error(f"Failed to send message {message.id}: {e}")
            self._stats.error_count += 1
            return False
    
    async def send_message_sync(self, message: Message) -> bool:
        """
        Send a message synchronously by converting it to an event.
        
        Args:
            message: Message to send
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        try:
            # Validate outgoing message
            if not validate_message(message):
                logger.error(f"Outgoing message {message.id} failed validation")
                self._stats.validation_error_count += 1
                return False
            
            # Convert message to event
            event = Event(
                event_type=f"message.{message.topic}" if message.topic else "message",
                payload={
                    "message_id": message.id,
                    "message_type": message.type.value,
                    "source": message.source,
                    "destination": message.destination,
                    "topic": message.topic,
                    "payload": message.payload,
                    "timestamp": message.timestamp.isoformat(),
                    "correlation_id": message.correlation_id,
                    "reply_to": message.reply_to,
                    "priority": message.priority,
                    "ttl": message.ttl,
                    "metadata": message.metadata
                },
                source=message.source,
                correlation_id=message.correlation_id
            )
            
            await publish(event)
            logger.debug(f"Message {message.id} sent as event")
            return True
        except ValidationError as e:
            logger.error(f"Message {message.id} failed validation: {e}")
            self._stats.validation_error_count += 1
            return False
        except Exception as e:
            logger.error(f"Failed to send message {message.id} as event: {e}")
            return False
    
    def register_handler(self, topic: str, handler: Callable[[Message], Any]) -> None:
        """
        Register a message handler for a specific topic.
        
        Args:
            topic: Topic to handle
            handler: Handler function
        """
        self._handlers[topic] = handler
        logger.info(f"Handler registered for topic '{topic}'")
    
    def unregister_handler(self, topic: str) -> bool:
        """
        Unregister a message handler.
        
        Args:
            topic: Topic to unregister
            
        Returns:
            True if handler was unregistered, False if not found
        """
        if topic in self._handlers:
            del self._handlers[topic]
            logger.info(f"Handler unregistered for topic '{topic}'")
            return True
        return False
    
    async def start_processing(self) -> None:
        """Start processing messages from the queue."""
        if self._running:
            return
            
        self._running = True
        self._processing_task = asyncio.create_task(self._process_messages())
        logger.info(f"Message queue '{self.name}' processing started")
    
    async def stop_processing(self) -> None:
        """Stop processing messages from the queue."""
        if not self._running:
            return
            
        self._running = False
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
        logger.info(f"Message queue '{self.name}' processing stopped")
    
    async def _process_messages(self) -> None:
        """Process messages from the queue."""
        while self._running:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                
                # Validate and sanitize incoming message if enabled
                if self._validate_incoming or self._sanitize_incoming:
                    try:
                        if self._sanitize_incoming:
                            message = sanitize_message(message)
                        
                        if self._validate_incoming:
                            if not validate_message(message):
                                logger.error(f"Incoming message {message.id} failed validation")
                                self._stats.validation_error_count += 1
                                self._queue.task_done()
                                continue
                    except ValidationError as e:
                        logger.error(f"Message {message.id} failed validation/sanitization: {e}")
                        self._stats.validation_error_count += 1
                        self._queue.task_done()
                        continue
                
                # Check TTL if specified
                if message.ttl is not None:
                    expiry_time = message.timestamp.timestamp() + message.ttl
                    if datetime.now().timestamp() > expiry_time:
                        logger.warning(f"Message {message.id} expired during processing")
                        self._stats.error_count += 1
                        self._queue.task_done()
                        continue
                
                # Process message
                start_time = datetime.now()
                await self._handle_message(message)
                processing_time = (datetime.now() - start_time).total_seconds()
                
                # Update stats
                self._stats.processed_count += 1
                self._stats.last_processed = datetime.now()
                
                # Update average processing time
                if self._stats.processed_count == 1:
                    self._stats.average_processing_time = processing_time
                else:
                    # Running average
                    self._stats.average_processing_time = (
                        (self._stats.average_processing_time * (self._stats.processed_count - 1)) + 
                        processing_time
                    ) / self._stats.processed_count
                
                self._queue.task_done()
                
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                self._stats.error_count += 1
    
    async def _handle_message(self, message: Message) -> None:
        """
        Handle a message using registered handlers.
        
        Args:
            message: Message to handle
        """
        try:
            # Try topic-specific handler first
            if message.topic in self._handlers:
                handler = self._handlers[message.topic]
                if asyncio.iscoroutinefunction(handler):
                    await handler(message)
                else:
                    handler(message)
                return
            
            # Try default handler
            if "*" in self._handlers:
                handler = self._handlers["*"]
                if asyncio.iscoroutinefunction(handler):
                    await handler(message)
                else:
                    handler(message)
                return
            
            logger.warning(f"No handler found for message {message.id} with topic '{message.topic}'")
            
        except Exception as e:
            logger.error(f"Error in message handler for {message.id}: {e}")
            self._stats.error_count += 1
            
            # Send error message if reply_to is specified
            if message.reply_to:
                error_message = Message(
                    type=MessageType.ERROR,
                    source=self.name,
                    destination=message.source,
                    topic="error",
                    payload={
                        "original_message_id": message.id,
                        "error": str(e)
                    },
                    correlation_id=message.correlation_id
                )
                await self.send_message(error_message)
    
    def get_stats(self) -> QueueStats:
        """
        Get queue statistics.
        
        Returns:
            Queue statistics
        """
        # Update message count to reflect current queue size
        stats = QueueStats(
            queue_name=self._stats.queue_name,
            message_count=self._queue.qsize(),
            processed_count=self._stats.processed_count,
            error_count=self._stats.error_count,
            validation_error_count=self._stats.validation_error_count,
            last_processed=self._stats.last_processed,
            average_processing_time=self._stats.average_processing_time
        )
        return stats
    
    def serialize_message(self, message: Message) -> bytes:
        """
        Serialize a message to bytes.
        
        Args:
            message: Message to serialize
            
        Returns:
            Serialized message bytes
        """
        try:
            # Validate before serialization
            if not validate_message(message):
                logger.error(f"Message {message.id} failed validation before serialization")
                raise ValidationError("Message validation failed")
            
            if self._serialization_format == SerializationFormat.JSON:
                data = {
                    "id": message.id,
                    "type": message.type.value,
                    "source": message.source,
                    "destination": message.destination,
                    "topic": message.topic,
                    "payload": message.payload,
                    "timestamp": message.timestamp.isoformat(),
                    "correlation_id": message.correlation_id,
                    "reply_to": message.reply_to,
                    "priority": message.priority,
                    "ttl": message.ttl,
                    "metadata": message.metadata
                }
                return json.dumps(data).encode('utf-8')
            
            elif self._serialization_format == SerializationFormat.PICKLE:
                return pickle.dumps(message)
            
            else:
                raise ValueError(f"Unsupported serialization format: {self._serialization_format}")
                
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Failed to serialize message {message.id}: {e}")
            raise
    
    def deserialize_message(self, data: bytes) -> Message:
        """
        Deserialize bytes to a message.
        
        Args:
            data: Serialized message bytes
            
        Returns:
            Deserialized message
        """
        try:
            if self._serialization_format == SerializationFormat.JSON:
                json_data = json.loads(data.decode('utf-8'))
                message = Message(
                    id=json_data["id"],
                    type=MessageType(json_data["type"]),
                    source=json_data["source"],
                    destination=json_data["destination"],
                    topic=json_data["topic"],
                    payload=json_data["payload"],
                    timestamp=datetime.fromisoformat(json_data["timestamp"]),
                    correlation_id=json_data["correlation_id"],
                    reply_to=json_data["reply_to"],
                    priority=json_data["priority"],
                    ttl=json_data["ttl"],
                    metadata=json_data["metadata"]
                )
            
            elif self._serialization_format == SerializationFormat.PICKLE:
                message = pickle.loads(data)
            
            else:
                raise ValueError(f"Unsupported serialization format: {self._serialization_format}")
            
            # Validate after deserialization
            if not validate_message(message):
                logger.error(f"Deserialized message {message.id} failed validation")
                raise ValidationError("Deserialized message validation failed")
            
            # Sanitize if enabled
            if self._sanitize_incoming:
                message = sanitize_message(message)
            
            return message
                
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Failed to deserialize message: {e}")
            raise


class MessagingSystem:
    """Central messaging system coordinating multiple queues."""
    
    _instance: Optional['MessagingSystem'] = None
    
    def __init__(self):
        """Initialize the messaging system."""
        if MessagingSystem._instance is not None:
            raise RuntimeError("Use MessagingSystem.get_instance() to get the singleton instance")
            
        self._queues: Dict[str, MessageQueue] = {}
        self._running = False
        
        MessagingSystem._instance = self
    
    @classmethod
    def get_instance(cls) -> 'MessagingSystem':
        """
        Get the singleton instance of the messaging system.
        
        Returns:
            MessagingSystem instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def create_queue(self, name: str, max_size: int = 1000) -> MessageQueue:
        """
        Create a new message queue.
        
        Args:
            name: Queue name
            max_size: Maximum queue size
            
        Returns:
            Created message queue
        """
        if name in self._queues:
            logger.warning(f"Queue '{name}' already exists, returning existing queue")
            return self._queues[name]
        
        queue = MessageQueue(name, max_size)
        self._queues[name] = queue
        logger.info(f"Created message queue '{name}'")
        return queue
    
    def get_queue(self, name: str) -> Optional[MessageQueue]:
        """
        Get a message queue by name.
        
        Args:
            name: Queue name
            
        Returns:
            Message queue or None if not found
        """
        return self._queues.get(name)
    
    def delete_queue(self, name: str) -> bool:
        """
        Delete a message queue.
        
        Args:
            name: Queue name
            
        Returns:
            True if queue was deleted, False if not found
        """
        if name in self._queues:
            queue = self._queues[name]
            # Stop processing if running
            if queue._running:
                asyncio.create_task(queue.stop_processing())
            del self._queues[name]
            logger.info(f"Deleted message queue '{name}'")
            return True
        return False
    
    async def start_all_queues(self) -> None:
        """Start processing all queues."""
        if self._running:
            return
            
        self._running = True
        for queue in self._queues.values():
            await queue.start_processing()
        logger.info("All message queues started")
    
    async def stop_all_queues(self) -> None:
        """Stop processing all queues."""
        if not self._running:
            return
            
        self._running = False
        for queue in self._queues.values():
            await queue.stop_processing()
        logger.info("All message queues stopped")
    
    def get_all_stats(self) -> Dict[str, QueueStats]:
        """
        Get statistics for all queues.
        
        Returns:
            Dictionary of queue names to statistics
        """
        return {name: queue.get_stats() for name, queue in self._queues.items()}


# Convenience functions
def get_messaging_system() -> MessagingSystem:
    """Get the global messaging system instance."""
    return MessagingSystem.get_instance()


def create_queue(name: str, max_size: int = 1000) -> MessageQueue:
    """Create a message queue."""
    system = get_messaging_system()
    return system.create_queue(name, max_size)


def get_queue(name: str) -> Optional[MessageQueue]:
    """Get a message queue by name."""
    system = get_messaging_system()
    return system.get_queue(name)


async def send_message(queue_name: str, message: Message) -> bool:
    """Send a message to a queue."""
    queue = get_queue(queue_name)
    if queue:
        return await queue.send_message(message)
    return False


async def start_messaging_system() -> None:
    """Start the messaging system."""
    system = get_messaging_system()
    await system.start_all_queues()


async def stop_messaging_system() -> None:
    """Stop the messaging system."""
    system = get_messaging_system()
    await system.stop_all_queues()
