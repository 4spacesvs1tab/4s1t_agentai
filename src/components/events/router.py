"""
Enhanced event routing system for the 4S1T Agent AI framework.

Provides advanced event routing capabilities including transformation, batching, and prioritization.
"""
import asyncio
import logging
from typing import Dict, List, Callable, Any, Optional, Union, Pattern, Deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from collections import deque
import re
import time

from utils.logger import setup_logger
from components.events.event_bus import Event, EventPriority, get_event_bus, publish, subscribe

logger = setup_logger(__name__)


class RoutingStrategy(Enum):
    """Event routing strategies."""
    DIRECT = "direct"
    BATCH = "batch"
    TRANSFORM = "transform"
    FILTER = "filter"
    PRIORITY = "priority"


@dataclass
class RouteRule:
    """A rule for routing events."""
    source_pattern: Union[str, Pattern]
    target_type: str
    strategy: RoutingStrategy
    condition: Optional[Callable[[Event], bool]] = None
    transformer: Optional[Callable[[Event], Event]] = None
    priority: EventPriority = EventPriority.NORMAL
    batch_size: int = 1
    batch_timeout: float = 5.0  # seconds


@dataclass
class Batch:
    """A batch of events."""
    events: List[Event] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    target_type: str = ""
    
    def add_event(self, event: Event) -> None:
        """Add an event to the batch."""
        self.events.append(event)
    
    def is_full(self, max_size: int) -> bool:
        """Check if the batch is full."""
        return len(self.events) >= max_size


class EventRouter:
    """Advanced event router with transformation, batching, and prioritization capabilities."""
    
    _instance: Optional['EventRouter'] = None
    
    def __init__(self):
        """Initialize the event router."""
        if EventRouter._instance is not None:
            raise RuntimeError("Use EventRouter.get_instance() to get the singleton instance")
            
        self._rules: List[RouteRule] = []
        self._batches: Dict[str, Batch] = {}
        self._batch_timers: Dict[str, asyncio.Task] = {}
        self._running = False
        self._bus = get_event_bus()
        
        EventRouter._instance = self
    
    @classmethod
    def get_instance(cls) -> 'EventRouter':
        """
        Get the singleton instance of the event router.
        
        Returns:
            EventRouter instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def add_route_rule(self, rule: RouteRule) -> None:
        """
        Add a route rule.
        
        Args:
            rule: Route rule to add
        """
        self._rules.append(rule)
        logger.info(f"Added route rule: {rule.source_pattern} -> {rule.target_type}")
    
    def remove_route_rule(self, rule: RouteRule) -> bool:
        """
        Remove a route rule.
        
        Args:
            rule: Route rule to remove
            
        Returns:
            True if rule was removed, False if not found
        """
        try:
            self._rules.remove(rule)
            logger.info(f"Removed route rule: {rule.source_pattern} -> {rule.target_type}")
            return True
        except ValueError:
            return False
    
    async def route_event(self, event: Event) -> bool:
        """
        Route an event according to defined rules.
        
        Args:
            event: Event to route
            
        Returns:
            True if event was routed, False otherwise
        """
        try:
            routed = False
            
            for rule in self._rules:
                # Check if rule applies to this event
                if not self._rule_matches_event(rule, event):
                    continue
                
                # Apply rule strategy
                if rule.strategy == RoutingStrategy.DIRECT:
                    await self._route_direct(event, rule)
                elif rule.strategy == RoutingStrategy.BATCH:
                    await self._route_batch(event, rule)
                elif rule.strategy == RoutingStrategy.TRANSFORM:
                    await self._route_transform(event, rule)
                elif rule.strategy == RoutingStrategy.FILTER:
                    await self._route_filter(event, rule)
                elif rule.strategy == RoutingStrategy.PRIORITY:
                    await self._route_priority(event, rule)
                
                routed = True
            
            # If no rules matched, publish directly
            if not routed:
                await publish(event)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to route event {event.event_type}: {e}")
            return False
    
    def _rule_matches_event(self, rule: RouteRule, event: Event) -> bool:
        """
        Check if a rule matches an event.
        
        Args:
            rule: Route rule
            event: Event to check
            
        Returns:
            True if rule matches event, False otherwise
        """
        # Check source pattern
        if isinstance(rule.source_pattern, str):
            if not re.match(rule.source_pattern, event.event_type):
                return False
        elif isinstance(rule.source_pattern, Pattern):
            if not rule.source_pattern.match(event.event_type):
                return False
        
        # Check condition if specified
        if rule.condition and not rule.condition(event):
            return False
        
        return True
    
    async def _route_direct(self, event: Event, rule: RouteRule) -> None:
        """
        Route an event directly to a new type.
        
        Args:
            event: Event to route
            rule: Route rule
        """
        routed_event = Event(
            event_type=rule.target_type,
            payload=event.payload.copy(),
            source=event.source,
            priority=rule.priority,
            correlation_id=event.correlation_id
        )
        await publish(routed_event)
        logger.debug(f"Direct route: {event.event_type} -> {rule.target_type}")
    
    async def _route_batch(self, event: Event, rule: RouteRule) -> None:
        """
        Route an event by batching it.
        
        Args:
            event: Event to route
            rule: Route rule
        """
        # Create batch key
        batch_key = f"{rule.target_type}:{event.source or 'unknown'}"
        
        # Get or create batch
        if batch_key not in self._batches:
            self._batches[batch_key] = Batch(target_type=rule.target_type)
        
        batch = self._batches[batch_key]
        batch.add_event(event)
        
        # Cancel existing timer if any
        if batch_key in self._batch_timers:
            self._batch_timers[batch_key].cancel()
        
        # Check if batch is full
        if batch.is_full(rule.batch_size):
            await self._flush_batch(batch_key)
        else:
            # Set timer to flush batch after timeout
            async def flush_timer():
                await asyncio.sleep(rule.batch_timeout)
                if batch_key in self._batches:
                    await self._flush_batch(batch_key)
            
            self._batch_timers[batch_key] = asyncio.create_task(flush_timer())
        
        logger.debug(f"Batch route: {event.event_type} added to batch {batch_key}")
    
    async def _route_transform(self, event: Event, rule: RouteRule) -> None:
        """
        Route an event by transforming it.
        
        Args:
            event: Event to route
            rule: Route rule
        """
        if rule.transformer:
            try:
                transformed_event = rule.transformer(event)
                transformed_event.event_type = rule.target_type
                transformed_event.priority = rule.priority
                await publish(transformed_event)
                logger.debug(f"Transform route: {event.event_type} -> {rule.target_type}")
            except Exception as e:
                logger.error(f"Failed to transform event {event.event_type}: {e}")
        else:
            # Direct route if no transformer
            await self._route_direct(event, rule)
    
    async def _route_filter(self, event: Event, rule: RouteRule) -> None:
        """
        Route an event by filtering it.
        
        Args:
            event: Event to route
            rule: Route rule
        """
        # For filter strategy, we only route if it passes through
        # The filtering is already done in _rule_matches_event
        await self._route_direct(event, rule)
    
    async def _route_priority(self, event: Event, rule: RouteRule) -> None:
        """
        Route an event with priority adjustment.
        
        Args:
            event: Event to route
            rule: Route rule
        """
        routed_event = Event(
            event_type=rule.target_type,
            payload=event.payload.copy(),
            source=event.source,
            priority=rule.priority,
            correlation_id=event.correlation_id
        )
        await publish(routed_event)
        logger.debug(f"Priority route: {event.event_type} -> {rule.target_type} with priority {rule.priority}")
    
    async def _flush_batch(self, batch_key: str) -> None:
        """
        Flush a batch of events.
        
        Args:
            batch_key: Batch key to flush
        """
        if batch_key not in self._batches:
            return
        
        batch = self._batches[batch_key]
        
        # Remove timer if exists
        if batch_key in self._batch_timers:
            self._batch_timers[batch_key].cancel()
            del self._batch_timers[batch_key]
        
        # Create batch event
        if batch.events:
            batch_payload = {
                "events": [
                    {
                        "type": event.event_type,
                        "payload": event.payload,
                        "timestamp": event.timestamp.isoformat(),
                        "source": event.source,
                        "correlation_id": event.correlation_id
                    }
                    for event in batch.events
                ],
                "count": len(batch.events),
                "batch_created": batch.created_at.isoformat()
            }
            
            batch_event = Event(
                event_type=batch.target_type,
                payload=batch_payload,
                source="event_router",
                priority=EventPriority.NORMAL,
                correlation_id=batch.events[0].correlation_id if batch.events else None
            )
            
            await publish(batch_event)
            logger.debug(f"Flushed batch {batch_key} with {len(batch.events)} events")
        
        # Remove batch
        del self._batches[batch_key]
    
    async def flush_all_batches(self) -> None:
        """Flush all pending batches."""
        batch_keys = list(self._batches.keys())
        for batch_key in batch_keys:
            await self._flush_batch(batch_key)
        logger.info("Flushed all pending batches")
    
    async def start_routing(self) -> None:
        """Start the event router."""
        if self._running:
            return
        
        self._running = True
        logger.info("Event router started")
    
    async def stop_routing(self) -> None:
        """Stop the event router."""
        if not self._running:
            return
        
        # Flush all batches
        await self.flush_all_batches()
        
        self._running = False
        logger.info("Event router stopped")


# Predefined transformers
def create_field_mapper(field_mappings: Dict[str, str]) -> Callable[[Event], Event]:
    """
    Create a transformer that maps fields from one name to another.
    
    Args:
        field_mappings: Dictionary mapping source field names to target field names
        
    Returns:
        Transformer function
    """
    def transformer(event: Event) -> Event:
        new_payload = {}
        for source_field, target_field in field_mappings.items():
            if source_field in event.payload:
                new_payload[target_field] = event.payload[source_field]
            else:
                new_payload[target_field] = None
        return Event(
            event_type=event.event_type,
            payload=new_payload,
            source=event.source,
            priority=event.priority,
            correlation_id=event.correlation_id
        )
    return transformer


def create_enricher(additional_fields: Dict[str, Any]) -> Callable[[Event], Event]:
    """
    Create a transformer that enriches events with additional fields.
    
    Args:
        additional_fields: Dictionary of fields to add to the event
        
    Returns:
        Transformer function
    """
    def transformer(event: Event) -> Event:
        new_payload = event.payload.copy()
        new_payload.update(additional_fields)
        return Event(
            event_type=event.event_type,
            payload=new_payload,
            source=event.source,
            priority=event.priority,
            correlation_id=event.correlation_id
        )
    return transformer


def create_filter(condition: Callable[[Event], bool]) -> Callable[[Event], Event]:
    """
    Create a transformer that filters events based on a condition.
    
    Args:
        condition: Function that returns True if event should pass through
        
    Returns:
        Transformer function
    """
    def transformer(event: Event) -> Event:
        if not condition(event):
            raise ValueError("Event filtered out")
        return event
    return transformer


# Convenience functions
def get_event_router() -> EventRouter:
    """Get the global event router instance."""
    return EventRouter.get_instance()


async def route_event(event: Event) -> bool:
    """Route an event globally."""
    router = get_event_router()
    return await router.route_event(event)


async def add_route_rule(rule: RouteRule) -> None:
    """Add a route rule globally."""
    router = get_event_router()
    router.add_route_rule(rule)


async def start_event_routing() -> None:
    """Start event routing globally."""
    router = get_event_router()
    await router.start_routing()


async def stop_event_routing() -> None:
    """Stop event routing globally."""
    router = get_event_router()
    await router.stop_routing()
