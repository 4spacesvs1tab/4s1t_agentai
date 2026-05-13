"""
Performance monitoring system for the 4S1T Agent AI framework.

Provides instrumentation, metrics collection, dashboard integration, and alerting for system performance.
"""
import asyncio
import time
import logging
from typing import Dict, List, Any, Optional, Callable, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json
import psutil
import threading

from utils.logger import setup_logger
from components.events.event_bus import Event, get_event_bus, publish

logger = setup_logger(__name__)


class MetricType(Enum):
    """Types of performance metrics."""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


class MetricUnit(Enum):
    """Units for metrics."""
    COUNT = "count"
    SECONDS = "seconds"
    BYTES = "bytes"
    PERCENT = "percent"
    NONE = "none"


@dataclass
class Metric:
    """A performance metric."""
    name: str
    type: MetricType
    unit: MetricUnit
    value: Union[int, float]
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    description: str = ""


@dataclass
class HistogramBucket:
    """A bucket in a histogram."""
    upper_bound: float
    count: int = 0


@dataclass
class HistogramMetric(Metric):
    """A histogram metric."""
    buckets: List[HistogramBucket] = field(default_factory=list)
    sum: float = 0.0
    count: int = 0


@dataclass
class SummaryMetric(Metric):
    """A summary metric."""
    quantiles: Dict[float, float] = field(default_factory=dict)
    sum: float = 0.0
    count: int = 0


class PerformanceMonitor:
    """Performance monitoring system with instrumentation and metrics collection."""
    
    _instance: Optional['PerformanceMonitor'] = None
    
    def __init__(self):
        """Initialize the performance monitor."""
        if PerformanceMonitor._instance is not None:
            raise RuntimeError("Use PerformanceMonitor.get_instance() to get the singleton instance")
            
        self._metrics: Dict[str, Metric] = {}
        self._metric_listeners: List[Callable[[Metric], Any]] = []
        self._collectors: List[Callable[[], List[Metric]]] = []
        self._alert_thresholds: Dict[str, Dict[str, Any]] = {}
        self._collection_interval: int = 60  # seconds
        self._running: bool = False
        self._collection_task: Optional[asyncio.Task] = None
        self._bus = get_event_bus()
        
        # Initialize system metrics collectors
        self._initialize_system_collectors()
        
        PerformanceMonitor._instance = self
    
    @classmethod
    def get_instance(cls) -> 'PerformanceMonitor':
        """
        Get the singleton instance of the performance monitor.
        
        Returns:
            PerformanceMonitor instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def _initialize_system_collectors(self) -> None:
        """Initialize built-in system metrics collectors."""
        # CPU usage collector
        def collect_cpu_metrics() -> List[Metric]:
            cpu_percent = psutil.cpu_percent(interval=1)
            return [
                Metric(
                    name="system_cpu_usage",
                    type=MetricType.GAUGE,
                    unit=MetricUnit.PERCENT,
                    value=cpu_percent,
                    description="Current CPU usage percentage"
                )
            ]
        
        # Memory usage collector
        def collect_memory_metrics() -> List[Metric]:
            memory = psutil.virtual_memory()
            return [
                Metric(
                    name="system_memory_usage",
                    type=MetricType.GAUGE,
                    unit=MetricUnit.PERCENT,
                    value=memory.percent,
                    description="Current memory usage percentage"
                ),
                Metric(
                    name="system_memory_available_bytes",
                    type=MetricType.GAUGE,
                    unit=MetricUnit.BYTES,
                    value=memory.available,
                    description="Available memory in bytes"
                )
            ]
        
        # Disk usage collector
        def collect_disk_metrics() -> List[Metric]:
            disk = psutil.disk_usage("/")
            return [
                Metric(
                    name="system_disk_usage",
                    type=MetricType.GAUGE,
                    unit=MetricUnit.PERCENT,
                    value=(disk.used / disk.total) * 100,
                    description="Current disk usage percentage"
                ),
                Metric(
                    name="system_disk_free_bytes",
                    type=MetricType.GAUGE,
                    unit=MetricUnit.BYTES,
                    value=disk.free,
                    description="Free disk space in bytes"
                )
            ]
        
        # Network I/O collector
        def collect_network_metrics() -> List[Metric]:
            net_io = psutil.net_io_counters()
            return [
                Metric(
                    name="system_network_bytes_sent",
                    type=MetricType.COUNTER,
                    unit=MetricUnit.BYTES,
                    value=net_io.bytes_sent,
                    description="Total bytes sent over network"
                ),
                Metric(
                    name="system_network_bytes_recv",
                    type=MetricType.COUNTER,
                    unit=MetricUnit.BYTES,
                    value=net_io.bytes_recv,
                    description="Total bytes received over network"
                )
            ]
        
        # Register collectors
        self.register_collector(collect_cpu_metrics)
        self.register_collector(collect_memory_metrics)
        self.register_collector(collect_disk_metrics)
        self.register_collector(collect_network_metrics)
    
    def instrument_function(
        self, 
        name: str, 
        unit: MetricUnit = MetricUnit.SECONDS,
        labels: Optional[Dict[str, str]] = None
    ):
        """
        Decorator for instrumenting function execution time.
        
        Args:
            name: Metric name
            unit: Metric unit
            labels: Metric labels
        """
        def decorator(func):
            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = await func(*args, **kwargs)
                    return result
                finally:
                    duration = time.time() - start_time
                    self.record_histogram(
                        name=f"{name}_duration",
                        value=duration,
                        unit=unit,
                        labels=labels,
                        description=f"Execution time of {func.__name__}"
                    )
            
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    return result
                finally:
                    duration = time.time() - start_time
                    self.record_histogram(
                        name=f"{name}_duration",
                        value=duration,
                        unit=unit,
                        labels=labels,
                        description=f"Execution time of {func.__name__}"
                    )
            
            # Return appropriate wrapper based on function type
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            else:
                return sync_wrapper
        
        return decorator
    
    def instrument_block(
        self, 
        name: str, 
        unit: MetricUnit = MetricUnit.SECONDS,
        labels: Optional[Dict[str, str]] = None
    ):
        """
        Context manager for instrumenting code blocks.
        
        Args:
            name: Metric name
            unit: Metric unit
            labels: Metric labels
        """
        return InstrumentationContext(self, name, unit, labels)
    
    def increment_counter(
        self, 
        name: str, 
        value: Union[int, float] = 1,
        labels: Optional[Dict[str, str]] = None,
        description: str = ""
    ) -> None:
        """
        Increment a counter metric.
        
        Args:
            name: Metric name
            value: Value to increment by
            labels: Metric labels
            description: Metric description
        """
        metric_key = self._get_metric_key(name, labels)
        
        if metric_key in self._metrics and self._metrics[metric_key].type == MetricType.COUNTER:
            metric = self._metrics[metric_key]
            metric.value += value
            metric.timestamp = datetime.now()
        else:
            self._metrics[metric_key] = Metric(
                name=name,
                type=MetricType.COUNTER,
                unit=MetricUnit.COUNT,
                value=value,
                labels=labels or {},
                description=description
            )
        
        self._notify_listeners(self._metrics[metric_key])
    
    def set_gauge(
        self, 
        name: str, 
        value: Union[int, float],
        labels: Optional[Dict[str, str]] = None,
        unit: MetricUnit = MetricUnit.NONE,
        description: str = ""
    ) -> None:
        """
        Set a gauge metric.
        
        Args:
            name: Metric name
            value: Gauge value
            labels: Metric labels
            unit: Metric unit
            description: Metric description
        """
        metric_key = self._get_metric_key(name, labels)
        
        self._metrics[metric_key] = Metric(
            name=name,
            type=MetricType.GAUGE,
            unit=unit,
            value=value,
            labels=labels or {},
            description=description
        )
        
        self._notify_listeners(self._metrics[metric_key])
    
    def record_histogram(
        self, 
        name: str, 
        value: Union[int, float],
        unit: MetricUnit = MetricUnit.NONE,
        labels: Optional[Dict[str, str]] = None,
        description: str = "",
        buckets: Optional[List[float]] = None
    ) -> None:
        """
        Record a histogram metric.
        
        Args:
            name: Metric name
            value: Recorded value
            unit: Metric unit
            labels: Metric labels
            description: Metric description
            buckets: Histogram buckets (default: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0])
        """
        metric_key = self._get_metric_key(name, labels)
        
        # Default buckets for timing metrics
        if buckets is None:
            if unit == MetricUnit.SECONDS:
                buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
            else:
                buckets = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
        
        # Create histogram buckets
        histogram_buckets = [HistogramBucket(upper_bound=b) for b in buckets]
        # Add infinity bucket
        histogram_buckets.append(HistogramBucket(upper_bound=float('inf')))
        
        # Find appropriate bucket and increment
        for bucket in histogram_buckets:
            if value <= bucket.upper_bound:
                bucket.count += 1
                break
        
        # Create or update histogram metric
        if metric_key in self._metrics:
            metric = self._metrics[metric_key]
            if isinstance(metric, HistogramMetric):
                # Update existing histogram
                for i, bucket in enumerate(histogram_buckets):
                    if i < len(metric.buckets):
                        metric.buckets[i].count += bucket.count
                metric.sum += value
                metric.count += 1
                metric.timestamp = datetime.now()
            else:
                # Replace with histogram metric
                self._metrics[metric_key] = HistogramMetric(
                    name=name,
                    type=MetricType.HISTOGRAM,
                    unit=unit,
                    value=value,
                    labels=labels or {},
                    description=description,
                    buckets=histogram_buckets,
                    sum=value,
                    count=1
                )
        else:
            # Create new histogram metric
            self._metrics[metric_key] = HistogramMetric(
                name=name,
                type=MetricType.HISTOGRAM,
                unit=unit,
                value=value,
                labels=labels or {},
                description=description,
                buckets=histogram_buckets,
                sum=value,
                count=1
            )
        
        self._notify_listeners(self._metrics[metric_key])
        
        # Check for alerts
        self._check_metric_alerts(name, value, labels)
    
    def record_summary(
        self, 
        name: str, 
        value: Union[int, float],
        unit: MetricUnit = MetricUnit.NONE,
        labels: Optional[Dict[str, str]] = None,
        description: str = "",
        quantiles: Optional[List[float]] = None
    ) -> None:
        """
        Record a summary metric.
        
        Args:
            name: Metric name
            value: Recorded value
            unit: Metric unit
            labels: Metric labels
            description: Metric description
            quantiles: Quantiles to track (default: [0.5, 0.9, 0.95, 0.99])
        """
        metric_key = self._get_metric_key(name, labels)
        
        # Default quantiles
        if quantiles is None:
            quantiles = [0.5, 0.9, 0.95, 0.99]
        
        # Create or update summary metric
        if metric_key in self._metrics:
            metric = self._metrics[metric_key]
            if isinstance(metric, SummaryMetric):
                # Update existing summary
                metric.sum += value
                metric.count += 1
                metric.timestamp = datetime.now()
                # Note: Full quantile calculation would require more complex implementation
                # For simplicity, we're just storing the latest value for each quantile
                for q in quantiles:
                    metric.quantiles[q] = value
            else:
                # Replace with summary metric
                self._metrics[metric_key] = SummaryMetric(
                    name=name,
                    type=MetricType.SUMMARY,
                    unit=unit,
                    value=value,
                    labels=labels or {},
                    description=description,
                    quantiles={q: value for q in quantiles},
                    sum=value,
                    count=1
                )
        else:
            # Create new summary metric
            self._metrics[metric_key] = SummaryMetric(
                name=name,
                type=MetricType.SUMMARY,
                unit=unit,
                value=value,
                labels=labels or {},
                description=description,
                quantiles={q: value for q in quantiles},
                sum=value,
                count=1
            )
        
        self._notify_listeners(self._metrics[metric_key])
    
    def _get_metric_key(self, name: str, labels: Optional[Dict[str, str]]) -> str:
        """
        Generate a unique key for a metric.
        
        Args:
            name: Metric name
            labels: Metric labels
            
        Returns:
            Metric key
        """
        if not labels:
            return name
        
        # Sort labels for consistent key generation
        label_parts = [f"{k}={v}" for k, v in sorted(labels.items())]
        return f"{name}{{{','.join(label_parts)}}}"
    
    def register_collector(self, collector: Callable[[], List[Metric]]) -> None:
        """
        Register a metrics collector.
        
        Args:
            collector: Function that returns a list of metrics
        """
        self._collectors.append(collector)
        logger.info("Registered metrics collector")
    
    def add_metric_listener(self, listener: Callable[[Metric], Any]) -> None:
        """
        Add a metric listener.
        
        Args:
            listener: Listener function to add
        """
        self._metric_listeners.append(listener)
        logger.debug("Added metric listener")
    
    def remove_metric_listener(self, listener: Callable[[Metric], Any]) -> bool:
        """
        Remove a metric listener.
        
        Args:
            listener: Listener function to remove
            
        Returns:
            True if listener was removed, False if not found
        """
        try:
            self._metric_listeners.remove(listener)
            logger.debug("Removed metric listener")
            return True
        except ValueError:
            return False
    
    def set_alert_threshold(
        self, 
        metric_name: str, 
        threshold: Union[int, float], 
        operator: str = ">",
        duration: int = 0,  # seconds
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Set an alert threshold for a metric.
        
        Args:
            metric_name: Metric name
            threshold: Threshold value
            operator: Comparison operator ("<", ">", "<=", ">=", "==", "!=")
            duration: Duration the threshold must be exceeded (0 for immediate)
            labels: Metric labels
        """
        metric_key = self._get_metric_key(metric_name, labels)
        self._alert_thresholds[metric_key] = {
            "threshold": threshold,
            "operator": operator,
            "duration": duration,
            "violations": []  # Track violation timestamps
        }
        logger.info(f"Set alert threshold for {metric_name}: {operator} {threshold}")
    
    def _check_metric_alerts(
        self, 
        metric_name: str, 
        value: Union[int, float], 
        labels: Optional[Dict[str, str]]
    ) -> None:
        """
        Check if a metric value violates any alert thresholds.
        
        Args:
            metric_name: Metric name
            value: Metric value
            labels: Metric labels
        """
        metric_key = self._get_metric_key(metric_name, labels)
        
        if metric_key not in self._alert_thresholds:
            return
        
        threshold_info = self._alert_thresholds[metric_key]
        threshold = threshold_info["threshold"]
        operator = threshold_info["operator"]
        duration = threshold_info["duration"]
        
        # Check if threshold is violated
        violation = False
        if operator == ">":
            violation = value > threshold
        elif operator == "<":
            violation = value < threshold
        elif operator == ">=":
            violation = value >= threshold
        elif operator == "<=":
            violation = value <= threshold
        elif operator == "==":
            violation = value == threshold
        elif operator == "!=":
            violation = value != threshold
        
        if violation:
            # Record violation
            threshold_info["violations"].append(datetime.now())
            
            # Check if duration requirement is met
            if duration > 0:
                # Remove old violations
                cutoff_time = datetime.now() - timedelta(seconds=duration)
                threshold_info["violations"] = [
                    v for v in threshold_info["violations"] 
                    if v >= cutoff_time
                ]
                
                # Check if violation has persisted for required duration
                if len(threshold_info["violations"]) > 0:
                    first_violation = min(threshold_info["violations"])
                    if (datetime.now() - first_violation).total_seconds() >= duration:
                        self._trigger_alert(metric_name, value, threshold_info, labels)
            else:
                # Immediate alert
                self._trigger_alert(metric_name, value, threshold_info, labels)
        else:
            # Reset violations if threshold is no longer violated
            threshold_info["violations"].clear()
    
    def _trigger_alert(
        self, 
        metric_name: str, 
        value: Union[int, float], 
        threshold_info: Dict[str, Any],
        labels: Optional[Dict[str, str]]
    ) -> None:
        """
        Trigger a performance alert.
        
        Args:
            metric_name: Metric name
            value: Metric value that triggered the alert
            threshold_info: Threshold information
            labels: Metric labels
        """
        logger.warning(
            f"Performance alert: {metric_name} = {value} {threshold_info['operator']} "
            f"{threshold_info['threshold']}"
        )
        
        # Publish alert event
        try:
            event = Event(
                event_type="performance.alert",
                payload={
                    "metric_name": metric_name,
                    "value": value,
                    "threshold": threshold_info["threshold"],
                    "operator": threshold_info["operator"],
                    "labels": labels,
                    "timestamp": datetime.now().isoformat()
                },
                source="performance_monitor"
            )
            asyncio.create_task(publish(event))
        except Exception as e:
            logger.error(f"Failed to publish performance alert: {e}")
    
    def _notify_listeners(self, metric: Metric) -> None:
        """
        Notify metric listeners.
        
        Args:
            metric: Metric to notify about
        """
        for listener in self._metric_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    asyncio.create_task(listener(metric))
                else:
                    listener(metric)
            except Exception as e:
                logger.error(f"Error in metric listener: {e}")
    
    def collect_metrics(self) -> List[Metric]:
        """
        Collect all metrics from registered collectors.
        
        Returns:
            List of collected metrics
        """
        all_metrics = list(self._metrics.values())
        
        # Collect from registered collectors
        for collector in self._collectors:
            try:
                metrics = collector()
                all_metrics.extend(metrics)
            except Exception as e:
                logger.error(f"Error in metrics collector: {e}")
        
        return all_metrics
    
    def get_metric(self, name: str, labels: Optional[Dict[str, str]] = None) -> Optional[Metric]:
        """
        Get a metric by name and labels.
        
        Args:
            name: Metric name
            labels: Metric labels
            
        Returns:
            Metric or None if not found
        """
        metric_key = self._get_metric_key(name, labels)
        return self._metrics.get(metric_key)
    
    def get_metrics_by_prefix(self, prefix: str) -> List[Metric]:
        """
        Get all metrics with names starting with a prefix.
        
        Args:
            prefix: Metric name prefix
            
        Returns:
            List of matching metrics
        """
        return [m for m in self._metrics.values() if m.name.startswith(prefix)]
    
    def clear_metrics(self) -> None:
        """Clear all stored metrics."""
        self._metrics.clear()
        logger.info("Cleared all metrics")
    
    async def start_collection(self, interval: int = 60) -> None:
        """
        Start periodic metrics collection.
        
        Args:
            interval: Collection interval in seconds
        """
        if self._running:
            return
        
        self._collection_interval = interval
        self._running = True
        self._collection_task = asyncio.create_task(self._collection_loop())
        logger.info(f"Started metrics collection with {interval}s interval")
    
    async def stop_collection(self) -> None:
        """Stop periodic metrics collection."""
        if not self._running:
            return
        
        self._running = False
        if self._collection_task:
            self._collection_task.cancel()
            try:
                await self._collection_task
            except asyncio.CancelledError:
                pass
        logger.info("Stopped metrics collection")
    
    async def _collection_loop(self) -> None:
        """Main metrics collection loop."""
        while self._running:
            try:
                # Collect metrics
                metrics = self.collect_metrics()
                
                # Check for alerts
                self._check_collection_alerts(metrics)
                
                # Publish metrics event
                if metrics:
                    event = Event(
                        event_type="performance.metrics",
                        payload={
                            "metrics": [
                                {
                                    "name": m.name,
                                    "type": m.type.value,
                                    "unit": m.unit.value,
                                    "value": m.value,
                                    "labels": m.labels,
                                    "timestamp": m.timestamp.isoformat()
                                }
                                for m in metrics
                            ],
                            "timestamp": datetime.now().isoformat()
                        },
                        source="performance_monitor"
                    )
                    await publish(event)
                
                # Wait for next collection
                await asyncio.sleep(self._collection_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in metrics collection loop: {e}")
                await asyncio.sleep(self._collection_interval)
    
    def _check_collection_alerts(self, metrics: List[Metric]) -> None:
        """
        Check for alerts based on collected metrics.
        
        Args:
            metrics: List of collected metrics
        """
        for metric in metrics:
            self._check_metric_alerts(metric.name, metric.value, metric.labels)


class InstrumentationContext:
    """Context manager for instrumenting code blocks."""
    
    def __init__(
        self, 
        monitor: PerformanceMonitor,
        name: str,
        unit: MetricUnit = MetricUnit.SECONDS,
        labels: Optional[Dict[str, str]] = None
    ):
        self.monitor = monitor
        self.name = name
        self.unit = unit
        self.labels = labels
        self.start_time = None
    
    def __enter__(self):
        """Enter the context."""
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context, recording the duration."""
        if self.start_time is not None:
            duration = time.time() - self.start_time
            self.monitor.record_histogram(
                name=f"{self.name}_duration",
                value=duration,
                unit=self.unit,
                labels=self.labels,
                description=f"Execution time of instrumented block"
            )


# Convenience functions
def get_performance_monitor() -> PerformanceMonitor:
    """Get the global performance monitor instance."""
    return PerformanceMonitor.get_instance()


def instrument_function(
    name: str, 
    unit: MetricUnit = MetricUnit.SECONDS,
    labels: Optional[Dict[str, str]] = None
):
    """Instrument a function globally."""
    monitor = get_performance_monitor()
    return monitor.instrument_function(name, unit, labels)


def instrument_block(
    name: str, 
    unit: MetricUnit = MetricUnit.SECONDS,
    labels: Optional[Dict[str, str]] = None
):
    """Instrument a code block globally."""
    monitor = get_performance_monitor()
    return monitor.instrument_block(name, unit, labels)


def increment_counter(
    name: str, 
    value: Union[int, float] = 1,
    labels: Optional[Dict[str, str]] = None,
    description: str = ""
) -> None:
    """Increment a counter globally."""
    monitor = get_performance_monitor()
    monitor.increment_counter(name, value, labels, description)


def set_gauge(
    name: str, 
    value: Union[int, float],
    labels: Optional[Dict[str, str]] = None,
    unit: MetricUnit = MetricUnit.NONE,
    description: str = ""
) -> None:
    """Set a gauge globally."""
    monitor = get_performance_monitor()
    monitor.set_gauge(name, value, labels, unit, description)


def record_histogram(
    name: str, 
    value: Union[int, float],
    unit: MetricUnit = MetricUnit.NONE,
    labels: Optional[Dict[str, str]] = None,
    description: str = "",
    buckets: Optional[List[float]] = None
) -> None:
    """Record a histogram globally."""
    monitor = get_performance_monitor()
    monitor.record_histogram(name, value, unit, labels, description, buckets)


def record_summary(
    name: str, 
    value: Union[int, float],
    unit: MetricUnit = MetricUnit.NONE,
    labels: Optional[Dict[str, str]] = None,
    description: str = "",
    quantiles: Optional[List[float]] = None
) -> None:
    """Record a summary globally."""
    monitor = get_performance_monitor()
    monitor.record_summary(name, value, unit, labels, description, quantiles)


def set_alert_threshold(
    metric_name: str, 
    threshold: Union[int, float], 
    operator: str = ">",
    duration: int = 0,
    labels: Optional[Dict[str, str]] = None
) -> None:
    """Set an alert threshold globally."""
    monitor = get_performance_monitor()
    monitor.set_alert_threshold(metric_name, threshold, operator, duration, labels)


async def start_performance_monitoring(interval: int = 60) -> None:
    """Start performance monitoring globally."""
    monitor = get_performance_monitor()
    await monitor.start_collection(interval)


async def stop_performance_monitoring() -> None:
    """Stop performance monitoring globally."""
    monitor = get_performance_monitor()
    await monitor.stop_collection()
