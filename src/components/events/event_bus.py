"""
Event bus implementation for the 4S1T Agent AI framework.

Provides a publish-subscribe mechanism for inter-component communication.
"""
import asyncio
import logging
from typing import Dict, List, Callable, Any, Optional, Union, Pattern
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import weakref
import re

from utils.logger import setup_logger

logger = setup_logger(__name__)


class EventPriority(Enum):
    """Event priority levels."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class Event:
    """Base event class."""
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    source: Optional[str] = None
    priority: EventPriority = EventPriority.NORMAL
    correlation_id: Optional[str] = None


@dataclass
class EventSubscription:
    """Event subscription with filtering capabilities."""
    event_type: str
    handler: Callable[[Event], Any]
    pattern: Optional[Pattern] = None
    filter_func: Optional[Callable[[Event], bool]] = None
    priority: EventPriority = EventPriority.NORMAL


class EventBus:
    """Central event bus for publish-subscribe communication."""
    
    _instance: Optional['EventBus'] = None
    
    def __init__(self):
        """Initialize the event bus."""
        if EventBus._instance is not None:
            raise RuntimeError("Use EventBus.get_instance() to get the singleton instance")
            
        self._subscribers: Dict[str, List[EventSubscription]] = {}
        self._wildcard_subscribers: List[EventSubscription] = []
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._processing_task: Optional[asyncio.Task] = None
        
        EventBus._instance = self
    
    @classmethod
    def get_instance(cls) -> 'EventBus':
        """
        Get the singleton instance of the event bus.
        
        Returns:
            EventBus instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def subscribe(
        self, 
        event_type: str, 
        handler: Callable[[Event], Any],
        pattern: Optional[Union[str, Pattern]] = None,
        filter_func: Optional[Callable[[Event], bool]] = None,
        priority: EventPriority = EventPriority.NORMAL
    ) -> None:
        """
        Subscribe to events of a specific type with optional filtering.
        
        Args:
            event_type: Type of events to subscribe to
            handler: Function to call when event is published
            pattern: Regex pattern to match against event type
            filter_func: Function to filter events
            priority: Priority of the subscription
        """
        # Compile pattern if it's a string
        compiled_pattern = None
        if isinstance(pattern, str):
            compiled_pattern = re.compile(pattern)
        elif isinstance(pattern, Pattern):
            compiled_pattern = pattern
        
        subscription = EventSubscription(
            event_type=event_type,
            handler=handler,
            pattern=compiled_pattern,
            filter_func=filter_func,
            priority=priority
        )
        
        if event_type == "*":
            # Wildcard subscription
            self._wildcard_subscribers.append(subscription)
            self._wildcard_subscribers.sort(key=lambda s: s.priority.value, reverse=True)
            logger.debug(f"Added wildcard subscriber with priority {priority}")
        else:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(subscription)
            self._subscribers[event_type].sort(key=lambda s: s.priority.value, reverse=True)
            logger.debug(f"Subscribed to event type: {event_type} with priority {priority}")
    
    def unsubscribe(self, event_type: str, handler: Callable[[Event], Any]) -> bool:
        """
        Unsubscribe from events of a specific type.
        
        Args:
            event_type: Type of events to unsubscribe from
            handler: Function to remove from subscribers
            
        Returns:
            True if unsubscribed, False if not found
        """
        if event_type == "*":
            # Remove from wildcard subscribers
            for i, subscription in enumerate(self._wildcard_subscribers):
                if subscription.handler == handler:
                    self._wildcard_subscribers.pop(i)
                    logger.debug("Removed wildcard subscriber")
                    return True
            return False
        else:
            # Remove from type-specific subscribers
            if event_type in self._subscribers:
                subscriptions = self._subscribers[event_type]
                for i, subscription in enumerate(subscriptions):
                    if subscription.handler == handler:
                        subscriptions.pop(i)
                        logger.debug(f"Unsubscribed from event type: {event_type}")
                        # Clean up empty lists
                        if not subscriptions:
                            del self._subscribers[event_type]
                        return True
            return False
    
    async def publish(self, event: Event) -> None:
        """
        Publish an event to all subscribers.
        
        Args:
            event: Event to publish
        """
        logger.debug(f"Publishing event: {event.event_type}")
        
        # Notify type-specific subscribers
        notified_handlers = set()
        
        if event.event_type in self._subscribers:
            for subscription in self._subscribers[event.event_type]:
                # Apply filtering
                if not self._should_notify_subscription(subscription, event):
                    continue
                
                handler = subscription.handler
                if handler in notified_handlers:
                    continue
                    
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(event)
                    else:
                        handler(event)
                    notified_handlers.add(handler)
                except Exception as e:
                    logger.error(f"Error in event handler for {event.event_type}: {str(e)}")
        
        # Notify wildcard subscribers
        for subscription in self._wildcard_subscribers:
            # Apply filtering
            if not self._should_notify_subscription(subscription, event):
                continue
            
            handler = subscription.handler
            if handler in notified_handlers:
                continue
                
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
                notified_handlers.add(handler)
            except Exception as e:
                logger.error(f"Error in wildcard event handler: {str(e)}")
    
    def _should_notify_subscription(self, subscription: EventSubscription, event: Event) -> bool:
        """
        Determine if a subscription should be notified of an event.
        
        Args:
            subscription: Event subscription
            event: Event to check
            
        Returns:
            True if subscription should be notified, False otherwise
        """
        # Check pattern matching
        if subscription.pattern and not subscription.pattern.match(event.event_type):
            return False
        
        # Check custom filter function
        if subscription.filter_func and not subscription.filter_func(event):
            return False
        
        return True
    
    async def publish_async(self, event: Event) -> None:
        """
        Publish an event asynchronously by putting it in the queue.
        
        Args:
            event: Event to publish
        """
        await self._event_queue.put(event)
    
    async def route_event(self, event: Event, route_rules: Dict[str, str]) -> None:
        """
        Route an event based on routing rules.
        
        Args:
            event: Event to route
            route_rules: Dictionary mapping event types to new event types
        """
        new_event_type = route_rules.get(event.event_type)
        if new_event_type:
            routed_event = Event(
                event_type=new_event_type,
                payload=event.payload.copy(),
                source=event.source,
                priority=event.priority,
                correlation_id=event.correlation_id
            )
            await self.publish(routed_event)
        else:
            await self.publish(event)
    
    async def transform_and_publish(self, event: Event, transformer: Callable[[Event], Event]) -> None:
        """
        Transform an event and publish the transformed version.
        
        Args:
            event: Original event
            transformer: Function to transform the event
        """
        try:
            transformed_event = transformer(event)
            await self.publish(transformed_event)
        except Exception as e:
            logger.error(f"Error transforming event {event.event_type}: {str(e)}")
    
    async def start_processing(self) -> None:
        """Start processing events from the queue."""
        if self._running:
            return
            
        self._running = True
        self._processing_task = asyncio.create_task(self._process_events())
        logger.info("Event bus processing started")
    
    async def stop_processing(self) -> None:
        """Stop processing events from the queue."""
        if not self._running:
            return
            
        self._running = False
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
        logger.info("Event bus processing stopped")
    
    async def _process_events(self) -> None:
        """Process events from the queue."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                await self.publish(event)
                self._event_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error processing event from queue: {str(e)}")


# Convenience functions
def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    return EventBus.get_instance()


def subscribe(
    event_type: str, 
    handler: Callable[[Event], Any],
    pattern: Optional[Union[str, Pattern]] = None,
    filter_func: Optional[Callable[[Event], bool]] = None,
    priority: EventPriority = EventPriority.NORMAL
) -> None:
    """Subscribe to events globally."""
    bus = get_event_bus()
    bus.subscribe(event_type, handler, pattern, filter_func, priority)


def unsubscribe(event_type: str, handler: Callable[[Event], Any]) -> bool:
    """Unsubscribe from events globally."""
    bus = get_event_bus()
    return bus.unsubscribe(event_type, handler)


async def publish(event: Event) -> None:
    """Publish an event globally."""
    bus = get_event_bus()
    await bus.publish(event)


async def publish_async(event: Event) -> None:
    """Publish an event asynchronously globally."""
    bus = get_event_bus()
    await bus.publish_async(event)


async def route_event(event: Event, route_rules: Dict[str, str]) -> None:
    """Route an event globally based on routing rules."""
    bus = get_event_bus()
    await bus.route_event(event, route_rules)


async def transform_and_publish(event: Event, transformer: Callable[[Event], Event]) -> None:
    """Transform and publish an event globally."""
    bus = get_event_bus()
    await bus.transform_and_publish(event, transformer)
