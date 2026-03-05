"""
Telemetry Collection for 4S1T Agent AI Framework.

This module provides detailed telemetry collection and analysis capabilities
for troubleshooting distributed AI agents.
"""

import asyncio
import logging
import json
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import psutil
import gc

from ai.stability.agent_stability import AgentStatus
from components.events.event_bus import Event, get_event_bus, publish, subscribe
from components.performance.metrics import PerformanceMetrics

logger = logging.getLogger(__name__)


@dataclass
class TelemetryConfig:
    """Configuration for telemetry collection."""
    
    # Collection settings
    collect_interval_seconds: int = 30
    detailed_collection_interval_seconds: int = 300  # Every 5 minutes
    retention_days: int = 7
    
    # Metrics to collect
    collect_system_metrics: bool = True
    collect_memory_metrics: bool = True
    collect_performance_metrics: bool = True
    collect_event_metrics: bool = True
    
    # Sampling
    event_sampling_rate: float = 0.1  # Sample 10% of events
    max_events_per_batch: int = 1000
    
    # Storage
    max_telemetry_entries: int = 10000
    compress_old_data: bool = True


@dataclass
class SystemMetrics:
    """System-level metrics."""
    cpu_percent: float
    memory_percent: float
    memory_available_mb: float
    disk_usage_percent: float
    network_bytes_sent: int
    network_bytes_recv: int
    timestamp: datetime


@dataclass
class MemoryMetrics:
    """Memory-related metrics."""
    python_memory_mb: float
    garbage_collections: int
    object_counts: Dict[str, int]
    reference_cycles: int
    timestamp: datetime


@dataclass
class PerformanceMetricsSnapshot:
    """Performance metrics snapshot."""
    response_times_ms: List[float]
    throughput_ops_per_sec: float
    error_rates: Dict[str, float]
    resource_utilization: Dict[str, float]
    timestamp: datetime


@dataclass
class EventMetrics:
    """Event processing metrics."""
    events_processed: int
    events_failed: int
    event_types: Dict[str, int]
    average_processing_time_ms: float
    queue_depth: int
    timestamp: datetime


@dataclass
class TelemetryEntry:
    """A single telemetry entry."""
    entry_id: str
    timestamp: datetime
    agent_id: str
    entry_type: str
    data: Dict[str, Any]
    severity: str = "INFO"
    tags: List[str] = field(default_factory=list)


class TelemetryCollector:
    """
    Collects detailed telemetry for troubleshooting distributed AI agents.
    
    This class gathers system, memory, performance, and event metrics
    to provide comprehensive visibility into agent operations.
    """
    
    def __init__(self, agent_id: str, config: TelemetryConfig = None):
        """
        Initialize the telemetry collector.
        
        Args:
            agent_id: Unique identifier for this agent
            config: Telemetry configuration
        """
        self.agent_id = agent_id
        self.config = config or TelemetryConfig()
        
        # Metrics storage
        self.system_metrics_history: List[SystemMetrics] = []
        self.memory_metrics_history: List[MemoryMetrics] = []
        self.performance_metrics_history: List[PerformanceMetricsSnapshot] = []
        self.event_metrics_history: List[EventMetrics] = []
        self.custom_telemetry: List[TelemetryEntry] = []
        
        # Event tracking
        self.event_counts: Dict[str, int] = {}
        self.event_processing_times: Dict[str, List[float]] = {}
        self.total_events_processed = 0
        self.total_events_failed = 0
        
        # Event handling
        self.event_bus = get_event_bus()
        self.active_tasks: Dict[str, asyncio.Task] = {}
        
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        
        # Performance monitoring
        self.performance_monitor = PerformanceMetrics()
    
    async def initialize(self):
        """Initialize the telemetry collector and start collection processes."""
        try:
            # Subscribe to relevant events
            await self._setup_event_subscriptions()
            
            # Start collection processes
            self.active_tasks["collection"] = asyncio.create_task(self._collection_process())
            self.active_tasks["detailed_collection"] = asyncio.create_task(self._detailed_collection_process())
            
            self.logger.info(f"Telemetry collector initialized for agent {self.agent_id}")
            
        except Exception as e:
            self.logger.error(f"Error initializing telemetry collector: {e}")
            raise
    
    async def _setup_event_subscriptions(self):
        """Set up event subscriptions for telemetry collection."""
        try:
            # Subscribe to all events with wildcard subscription
            await subscribe("*", self._on_any_event, priority="LOW")
            
            # Subscribe to specific events we want to track closely
            await subscribe("agent.status_update", self._on_agent_status_update)
            await subscribe("agent.alert", self._on_agent_alert)
            await subscribe("health.check_result", self._on_health_check)
            await subscribe("performance.metric", self._on_performance_metric)
        except Exception as e:
            self.logger.error(f"Failed to set up event subscriptions: {e}")
    
    async def _on_any_event(self, event: Event):
        """Handle all events for telemetry collection."""
        try:
            # Sample events based on sampling rate
            import random
            if random.random() > self.config.event_sampling_rate:
                return
            
            # Track event counts
            self.event_counts[event.event_type] = self.event_counts.get(event.event_type, 0) + 1
            self.total_events_processed += 1
            
            # Track processing times if available
            if hasattr(event, '_processing_start_time'):
                processing_time = (
                    datetime.now() - event._processing_start_time
                ).total_seconds() * 1000  # Convert to milliseconds
                
                if event.event_type not in self.event_processing_times:
                    self.event_processing_times[event.event_type] = []
                self.event_processing_times[event.event_type].append(processing_time)
                
                # Keep only recent processing times (last 100)
                if len(self.event_processing_times[event.event_type]) > 100:
                    self.event_processing_times[event.event_type] = \
                        self.event_processing_times[event.event_type][-100:]
            
            # Add to custom telemetry with low severity
            telemetry_entry = TelemetryEntry(
                entry_id=f"event_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type="event",
                data={
                    "event_type": event.event_type,
                    "source": event.source,
                    "payload_size": len(str(event.payload)),
                    "correlation_id": event.correlation_id
                },
                severity="DEBUG",
                tags=["event_tracking"]
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error handling event for telemetry: {e}")
    
    async def _on_agent_status_update(self, event: Event):
        """Handle agent status update events."""
        try:
            status_info = event.payload
            
            # Create telemetry entry for status update
            telemetry_entry = TelemetryEntry(
                entry_id=f"status_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type="agent_status",
                data=status_info,
                severity="INFO",
                tags=["agent", "status"]
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error handling agent status update for telemetry: {e}")
    
    async def _on_agent_alert(self, event: Event):
        """Handle agent alert events."""
        try:
            alert_info = event.payload
            severity = alert_info.get("severity", "INFO").upper()
            
            # Create telemetry entry for alert
            telemetry_entry = TelemetryEntry(
                entry_id=f"alert_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type="agent_alert",
                data=alert_info,
                severity=severity,
                tags=["agent", "alert", severity.lower()]
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error handling agent alert for telemetry: {e}")
    
    async def _on_health_check(self, event: Event):
        """Handle health check results."""
        try:
            health_info = event.payload
            status = health_info.get("status", "unknown").upper()
            
            # Map health status to severity
            severity_map = {
                "HEALTHY": "INFO",
                "DEGRADED": "WARNING",
                "UNHEALTHY": "ERROR",
                "UNKNOWN": "INFO"
            }
            severity = severity_map.get(status, "INFO")
            
            # Create telemetry entry for health check
            telemetry_entry = TelemetryEntry(
                entry_id=f"health_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type="health_check",
                data=health_info,
                severity=severity,
                tags=["health", status.lower()]
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error handling health check for telemetry: {e}")
    
    async def _on_performance_metric(self, event: Event):
        """Handle performance metric events."""
        try:
            metric_info = event.payload
            
            # Create telemetry entry for performance metric
            telemetry_entry = TelemetryEntry(
                entry_id=f"perf_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type="performance",
                data=metric_info,
                severity="INFO",
                tags=["performance", metric_info.get("metric_type", "unknown")]
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error handling performance metric for telemetry: {e}")
    
    async def _collection_process(self):
        """Process for regular telemetry collection."""
        while True:
            try:
                # Collect system metrics
                if self.config.collect_system_metrics:
                    await self._collect_system_metrics()
                
                # Collect memory metrics
                if self.config.collect_memory_metrics:
                    await self._collect_memory_metrics()
                
                # Collect event metrics
                if self.config.collect_event_metrics:
                    await self._collect_event_metrics()
                
                await asyncio.sleep(self.config.collect_interval_seconds)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in telemetry collection process: {e}")
                await asyncio.sleep(self.config.collect_interval_seconds)
    
    async def _detailed_collection_process(self):
        """Process for detailed telemetry collection."""
        while True:
            try:
                # Collect performance metrics
                if self.config.collect_performance_metrics:
                    await self._collect_performance_metrics()
                
                # Prune old data
                await self._prune_old_data()
                
                await asyncio.sleep(self.config.detailed_collection_interval_seconds)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in detailed telemetry collection process: {e}")
                await asyncio.sleep(self.config.detailed_collection_interval_seconds)
    
    async def _collect_system_metrics(self):
        """Collect system-level metrics."""
        try:
            # CPU and memory usage
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Network stats
            net_io = psutil.net_io_counters()
            
            system_metrics = SystemMetrics(
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                memory_available_mb=memory.available / (1024 * 1024),
                disk_usage_percent=(disk.used / disk.total) * 100,
                network_bytes_sent=net_io.bytes_sent,
                network_bytes_recv=net_io.bytes_recv,
                timestamp=datetime.now()
            )
            
            self.system_metrics_history.append(system_metrics)
            
            # Keep only recent history
            cutoff_time = datetime.now() - timedelta(hours=1)
            self.system_metrics_history = [
                m for m in self.system_metrics_history 
                if m.timestamp > cutoff_time
            ]
            
            # Create telemetry entry
            telemetry_entry = TelemetryEntry(
                entry_id=f"system_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type="system_metrics",
                data={
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "memory_available_mb": memory.available / (1024 * 1024),
                    "disk_usage_percent": (disk.used / disk.total) * 100
                },
                severity="INFO",
                tags=["system", "metrics"]
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error collecting system metrics: {e}")
    
    async def _collect_memory_metrics(self):
        """Collect memory-related metrics."""
        try:
            # Python memory usage
            import sys
            python_memory_mb = sys.getsizeof(gc.get_objects()) / (1024 * 1024)
            
            # Garbage collection stats
            gc_stats = gc.get_stats()
            garbage_collections = sum(stat.get('collections', 0) for stat in gc_stats)
            
            # Object counts (simplified)
            object_counts = {}
            try:
                # Count objects by type
                obj_types = {}
                for obj in gc.get_objects():
                    obj_type = type(obj).__name__
                    obj_types[obj_type] = obj_types.get(obj_type, 0) + 1
                
                # Keep top 10 most common object types
                sorted_types = sorted(obj_types.items(), key=lambda x: x[1], reverse=True)
                object_counts = dict(sorted_types[:10])
            except Exception:
                # If we can't count objects, that's okay
                object_counts = {"error": "Unable to count objects"}
            
            # Reference cycles
            try:
                ref_cycles = len(gc.garbage)
            except Exception:
                ref_cycles = -1  # Unable to determine
            
            memory_metrics = MemoryMetrics(
                python_memory_mb=python_memory_mb,
                garbage_collections=garbage_collections,
                object_counts=object_counts,
                reference_cycles=ref_cycles,
                timestamp=datetime.now()
            )
            
            self.memory_metrics_history.append(memory_metrics)
            
            # Keep only recent history
            cutoff_time = datetime.now() - timedelta(hours=1)
            self.memory_metrics_history = [
                m for m in self.memory_metrics_history 
                if m.timestamp > cutoff_time
            ]
            
            # Create telemetry entry
            telemetry_entry = TelemetryEntry(
                entry_id=f"memory_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type="memory_metrics",
                data={
                    "python_memory_mb": python_memory_mb,
                    "garbage_collections": garbage_collections,
                    "reference_cycles": ref_cycles,
                    "top_object_types": object_counts
                },
                severity="INFO",
                tags=["memory", "metrics"]
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error collecting memory metrics: {e}")
    
    async def _collect_performance_metrics(self):
        """Collect performance metrics."""
        try:
            # Get performance metrics from the performance monitor
            perf_data = self.performance_monitor.get_metrics_summary()
            
            # Response times (if available)
            response_times = []
            if hasattr(self.performance_monitor, 'response_times'):
                response_times = list(getattr(self.performance_monitor, 'response_times', []))
            
            # Throughput (operations per second)
            throughput = perf_data.get("throughput", 0.0)
            
            # Error rates by type
            error_rates = perf_data.get("error_rates", {})
            
            # Resource utilization
            resource_utilization = perf_data.get("resource_utilization", {})
            
            performance_snapshot = PerformanceMetricsSnapshot(
                response_times_ms=response_times,
                throughput_ops_per_sec=throughput,
                error_rates=error_rates,
                resource_utilization=resource_utilization,
                timestamp=datetime.now()
            )
            
            self.performance_metrics_history.append(performance_snapshot)
            
            # Keep only recent history
            cutoff_time = datetime.now() - timedelta(hours=1)
            self.performance_metrics_history = [
                m for m in self.performance_metrics_history 
                if m.timestamp > cutoff_time
            ]
            
            # Create telemetry entry
            telemetry_entry = TelemetryEntry(
                entry_id=f"perf_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type="performance_metrics",
                data={
                    "response_times_ms": response_times[:100],  # Limit to 100 samples
                    "throughput_ops_per_sec": throughput,
                    "error_rates": error_rates,
                    "resource_utilization": resource_utilization
                },
                severity="INFO",
                tags=["performance", "metrics"]
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error collecting performance metrics: {e}")
    
    async def _collect_event_metrics(self):
        """Collect event processing metrics."""
        try:
            # Calculate average processing times
            avg_processing_times = {}
            for event_type, times in self.event_processing_times.items():
                if times:
                    avg_processing_times[event_type] = sum(times) / len(times)
            
            # Queue depth (approximate)
            queue_depth = getattr(self.event_bus, '_event_queue', None)
            if queue_depth and hasattr(queue_depth, 'qsize'):
                queue_depth = queue_depth.qsize()
            else:
                queue_depth = 0
            
            event_metrics = EventMetrics(
                events_processed=self.total_events_processed,
                events_failed=self.total_events_failed,
                event_types=self.event_counts.copy(),
                average_processing_time_ms=sum(avg_processing_times.values()) / len(avg_processing_times) if avg_processing_times else 0.0,
                queue_depth=queue_depth,
                timestamp=datetime.now()
            )
            
            self.event_metrics_history.append(event_metrics)
            
            # Keep only recent history
            cutoff_time = datetime.now() - timedelta(hours=1)
            self.event_metrics_history = [
                m for m in self.event_metrics_history 
                if m.timestamp > cutoff_time
            ]
            
            # Create telemetry entry
            telemetry_entry = TelemetryEntry(
                entry_id=f"events_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type="event_metrics",
                data={
                    "events_processed": self.total_events_processed,
                    "events_failed": self.total_events_failed,
                    "event_type_counts": self.event_counts,
                    "average_processing_times": avg_processing_times,
                    "queue_depth": queue_depth
                },
                severity="INFO",
                tags=["events", "metrics"]
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error collecting event metrics: {e}")
    
    async def _add_telemetry_entry(self, entry: TelemetryEntry):
        """Add a telemetry entry to storage."""
        try:
            self.custom_telemetry.append(entry)
            
            # Keep only recent entries within limits
            if len(self.custom_telemetry) > self.config.max_telemetry_entries:
                # Remove oldest entries
                excess_count = len(self.custom_telemetry) - self.config.max_telemetry_entries
                self.custom_telemetry = self.custom_telemetry[excess_count:]
            
            self.logger.debug(f"Added telemetry entry: {entry.entry_type}")
            
        except Exception as e:
            self.logger.error(f"Error adding telemetry entry: {e}")
    
    async def _prune_old_data(self):
        """Prune old telemetry data based on retention policy."""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.config.retention_days)
            
            # Prune system metrics
            self.system_metrics_history = [
                m for m in self.system_metrics_history 
                if m.timestamp > cutoff_date
            ]
            
            # Prune memory metrics
            self.memory_metrics_history = [
                m for m in self.memory_metrics_history 
                if m.timestamp > cutoff_date
            ]
            
            # Prune performance metrics
            self.performance_metrics_history = [
                m for m in self.performance_metrics_history 
                if m.timestamp > cutoff_date
            ]
            
            # Prune event metrics
            self.event_metrics_history = [
                m for m in self.event_metrics_history 
                if m.timestamp > cutoff_date
            ]
            
            # Prune custom telemetry
            self.custom_telemetry = [
                t for t in self.custom_telemetry 
                if t.timestamp > cutoff_date
            ]
            
            self.logger.debug("Old telemetry data pruned")
            
        except Exception as e:
            self.logger.error(f"Error pruning old telemetry data: {e}")
    
    def get_system_metrics_summary(self) -> Dict[str, Any]:
        """
        Get a summary of recent system metrics.
        
        Returns:
            Dict with system metrics summary
        """
        try:
            if not self.system_metrics_history:
                return {}
            
            latest = self.system_metrics_history[-1]
            recent = self.system_metrics_history[-10:] if len(self.system_metrics_history) >= 10 else self.system_metrics_history
            
            avg_cpu = sum(m.cpu_percent for m in recent) / len(recent)
            avg_memory = sum(m.memory_percent for m in recent) / len(recent)
            
            return {
                "latest": {
                    "cpu_percent": latest.cpu_percent,
                    "memory_percent": latest.memory_percent,
                    "memory_available_mb": latest.memory_available_mb,
                    "disk_usage_percent": latest.disk_usage_percent
                },
                "averages": {
                    "cpu_percent": avg_cpu,
                    "memory_percent": avg_memory
                },
                "history_length": len(self.system_metrics_history),
                "timestamp": latest.timestamp.isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting system metrics summary: {e}")
            return {}
    
    def get_memory_metrics_summary(self) -> Dict[str, Any]:
        """
        Get a summary of recent memory metrics.
        
        Returns:
            Dict with memory metrics summary
        """
        try:
            if not self.memory_metrics_history:
                return {}
            
            latest = self.memory_metrics_history[-1]
            recent = self.memory_metrics_history[-10:] if len(self.memory_metrics_history) >= 10 else self.memory_metrics_history
            
            avg_memory = sum(m.python_memory_mb for m in recent) / len(recent)
            
            return {
                "latest": {
                    "python_memory_mb": latest.python_memory_mb,
                    "garbage_collections": latest.garbage_collections,
                    "reference_cycles": latest.reference_cycles,
                    "top_object_types": latest.object_counts
                },
                "averages": {
                    "python_memory_mb": avg_memory
                },
                "history_length": len(self.memory_metrics_history),
                "timestamp": latest.timestamp.isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting memory metrics summary: {e}")
            return {}
    
    def get_performance_metrics_summary(self) -> Dict[str, Any]:
        """
        Get a summary of recent performance metrics.
        
        Returns:
            Dict with performance metrics summary
        """
        try:
            if not self.performance_metrics_history:
                return {}
            
            latest = self.performance_metrics_history[-1]
            
            return {
                "latest": {
                    "response_times_ms": latest.response_times_ms[:10],  # First 10 samples
                    "throughput_ops_per_sec": latest.throughput_ops_per_sec,
                    "error_rates": latest.error_rates,
                    "resource_utilization": latest.resource_utilization
                },
                "history_length": len(self.performance_metrics_history),
                "timestamp": latest.timestamp.isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting performance metrics summary: {e}")
            return {}
    
    def get_event_metrics_summary(self) -> Dict[str, Any]:
        """
        Get a summary of recent event metrics.
        
        Returns:
            Dict with event metrics summary
        """
        try:
            if not self.event_metrics_history:
                return {}
            
            latest = self.event_metrics_history[-1]
            recent = self.event_metrics_history[-10:] if len(self.event_metrics_history) >= 10 else self.event_metrics_history
            
            total_events_processed = sum(m.events_processed for m in recent)
            total_events_failed = sum(m.events_failed for m in recent)
            
            return {
                "latest": {
                    "events_processed": latest.events_processed,
                    "events_failed": latest.events_failed,
                    "event_types": latest.event_types,
                    "average_processing_time_ms": latest.average_processing_time_ms,
                    "queue_depth": latest.queue_depth
                },
                "totals": {
                    "events_processed": total_events_processed,
                    "events_failed": total_events_failed
                },
                "history_length": len(self.event_metrics_history),
                "timestamp": latest.timestamp.isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting event metrics summary: {e}")
            return {}
    
    def get_custom_telemetry(self, entry_type: Optional[str] = None, 
                           severity: Optional[str] = None,
                           limit: int = 100) -> List[TelemetryEntry]:
        """
        Get custom telemetry entries with optional filtering.
        
        Args:
            entry_type: Filter by entry type
            severity: Filter by severity
            limit: Maximum number of entries to return
            
        Returns:
            List of telemetry entries
        """
        try:
            filtered_entries = self.custom_telemetry
            
            if entry_type:
                filtered_entries = [
                    entry for entry in filtered_entries 
                    if entry.entry_type == entry_type
                ]
            
            if severity:
                filtered_entries = [
                    entry for entry in filtered_entries 
                    if entry.severity == severity.upper()
                ]
            
            # Return most recent entries first
            return filtered_entries[-limit:] if len(filtered_entries) > limit else filtered_entries
            
        except Exception as e:
            self.logger.error(f"Error getting custom telemetry: {e}")
            return []
    
    def get_telemetry_summary(self) -> Dict[str, Any]:
        """
        Get a comprehensive summary of all telemetry data.
        
        Returns:
            Dict with comprehensive telemetry summary
        """
        try:
            return {
                "agent_id": self.agent_id,
                "timestamp": datetime.now().isoformat(),
                "system_metrics": self.get_system_metrics_summary(),
                "memory_metrics": self.get_memory_metrics_summary(),
                "performance_metrics": self.get_performance_metrics_summary(),
                "event_metrics": self.get_event_metrics_summary(),
                "custom_telemetry_count": len(self.custom_telemetry),
                "collection_config": {
                    "collect_interval_seconds": self.config.collect_interval_seconds,
                    "retention_days": self.config.retention_days
                }
            }
            
        except Exception as e:
            self.logger.error(f"Error getting telemetry summary: {e}")
            return {}
    
    async def add_custom_telemetry(self, entry_type: str, data: Dict[str, Any], 
                                 severity: str = "INFO", tags: List[str] = None):
        """
        Add custom telemetry data.
        
        Args:
            entry_type: Type of telemetry entry
            data: Telemetry data
            severity: Severity level
            tags: Tags for categorization
        """
        try:
            telemetry_entry = TelemetryEntry(
                entry_id=f"custom_{datetime.now().timestamp()}",
                timestamp=datetime.now(),
                agent_id=self.agent_id,
                entry_type=entry_type,
                data=data,
                severity=severity.upper(),
                tags=tags or []
            )
            await self._add_telemetry_entry(telemetry_entry)
            
        except Exception as e:
            self.logger.error(f"Error adding custom telemetry: {e}")
    
    async def shutdown(self):
        """Shutdown the telemetry collector."""
        try:
            # Cancel all active tasks
            for task_name, task in self.active_tasks.items():
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            self.active_tasks.clear()
            
            self.logger.info(f"Telemetry collector shutdown for agent {self.agent_id}")
            
        except Exception as e:
            self.logger.error(f"Error shutting down telemetry collector: {e}")
