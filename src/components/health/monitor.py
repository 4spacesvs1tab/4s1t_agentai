"""
Health monitoring system for the 4S1T Agent AI framework.

Provides system and component health monitoring with alerting capabilities.
"""
import asyncio
import logging
from typing import Dict, List, Callable, Any, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import psutil
import time

from utils.logger import setup_logger
from components.events.event_bus import Event, get_event_bus, publish
from components.registry import get_registry

logger = setup_logger(__name__)


class HealthStatus(Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    component: str
    status: HealthStatus
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    response_time: float = 0.0


@dataclass
class ComponentHealth:
    """Health information for a component."""
    name: str
    status: HealthStatus
    last_check: datetime
    last_healthy: Optional[datetime] = None
    failure_count: int = 0
    consecutive_failures: int = 0
    check_results: List[HealthCheckResult] = field(default_factory=list)


class HealthMonitor:
    """System health monitoring service."""
    
    _instance: Optional['HealthMonitor'] = None
    
    def __init__(self):
        """Initialize the health monitor."""
        if HealthMonitor._instance is not None:
            raise RuntimeError("Use HealthMonitor.get_instance() to get the singleton instance")
            
        self._components: Dict[str, ComponentHealth] = {}
        self._health_checks: Dict[str, Callable[[], HealthCheckResult]] = {}
        self._alert_thresholds: Dict[str, int] = {}
        self._monitoring_interval: int = 30  # seconds
        self._running = False
        self._monitoring_task: Optional[asyncio.Task] = None
        
        HealthMonitor._instance = self
    
    @classmethod
    def get_instance(cls) -> 'HealthMonitor':
        """
        Get the singleton instance of the health monitor.
        
        Returns:
            HealthMonitor instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def register_health_check(
        self, 
        component_name: str, 
        check_func: Callable[[], HealthCheckResult],
        alert_threshold: int = 3
    ) -> None:
        """
        Register a health check function for a component.
        
        Args:
            component_name: Name of the component
            check_func: Function that performs the health check
            alert_threshold: Number of consecutive failures before alerting
        """
        self._health_checks[component_name] = check_func
        self._alert_thresholds[component_name] = alert_threshold
        logger.info(f"Registered health check for component: {component_name}")
    
    def unregister_health_check(self, component_name: str) -> bool:
        """
        Unregister a health check function.
        
        Args:
            component_name: Name of the component
            
        Returns:
            True if unregistered, False if not found
        """
        if component_name in self._health_checks:
            del self._health_checks[component_name]
            if component_name in self._alert_thresholds:
                del self._alert_thresholds[component_name]
            logger.info(f"Unregistered health check for component: {component_name}")
            return True
        return False
    
    async def check_component_health(self, component_name: str) -> HealthCheckResult:
        """
        Check the health of a specific component.
        
        Args:
            component_name: Name of the component to check
            
        Returns:
            Health check result
        """
        if component_name not in self._health_checks:
            return HealthCheckResult(
                component=component_name,
                status=HealthStatus.UNKNOWN,
                message=f"No health check registered for component: {component_name}"
            )
        
        check_func = self._health_checks[component_name]
        start_time = time.time()
        
        try:
            result = check_func()
            result.response_time = time.time() - start_time
            
            # Update component health tracking
            self._update_component_health(component_name, result)
            
            return result
        except Exception as e:
            result = HealthCheckResult(
                component=component_name,
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}",
                response_time=time.time() - start_time
            )
            
            # Update component health tracking
            self._update_component_health(component_name, result)
            
            return result
    
    async def check_all_health(self) -> Dict[str, HealthCheckResult]:
        """
        Check the health of all registered components.
        
        Returns:
            Dictionary of component names to health check results
        """
        results = {}
        for component_name in self._health_checks:
            results[component_name] = await self.check_component_health(component_name)
        return results
    
    def _update_component_health(self, component_name: str, result: HealthCheckResult) -> None:
        """
        Update internal health tracking for a component.
        
        Args:
            component_name: Name of the component
            result: Health check result
        """
        if component_name not in self._components:
            self._components[component_name] = ComponentHealth(
                name=component_name,
                status=HealthStatus.UNKNOWN,
                last_check=result.timestamp
            )
        
        component_health = self._components[component_name]
        component_health.last_check = result.timestamp
        component_health.status = result.status
        
        # Update failure counters
        if result.status == HealthStatus.HEALTHY:
            component_health.last_healthy = result.timestamp
            component_health.consecutive_failures = 0
        else:
            component_health.failure_count += 1
            component_health.consecutive_failures += 1
            
            # Check if we should send an alert
            threshold = self._alert_thresholds.get(component_name, 3)
            if component_health.consecutive_failures >= threshold:
                self._send_health_alert(component_name, result)
        
        # Keep only recent check results (last 10)
        component_health.check_results.append(result)
        if len(component_health.check_results) > 10:
            component_health.check_results.pop(0)
    
    def _send_health_alert(self, component_name: str, result: HealthCheckResult) -> None:
        """
        Send a health alert for a component.
        
        Args:
            component_name: Name of the component
            result: Health check result that triggered the alert
        """
        logger.warning(f"Health alert for component {component_name}: {result.message}")
        
        # Publish health alert event
        event = Event(
            event_type="health.alert",
            payload={
                "component": component_name,
                "status": result.status.value,
                "message": result.message,
                "timestamp": result.timestamp.isoformat(),
                "failure_count": self._components[component_name].consecutive_failures
            },
            source="health_monitor"
        )
        asyncio.create_task(publish(event))
    
    def get_system_health(self) -> HealthCheckResult:
        """
        Get overall system health.
        
        Returns:
            Overall system health check result
        """
        # Check system resources
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            
            details = {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "disk_percent": (disk.used / disk.total) * 100,
                "boot_time": datetime.fromtimestamp(psutil.boot_time()).isoformat()
            }
            
            # Determine overall status based on resource usage
            if cpu_percent > 90 or memory.percent > 90 or (disk.used / disk.total) > 0.95:
                status = HealthStatus.DEGRADED
                message = "System resources are high"
            elif cpu_percent > 70 or memory.percent > 70 or (disk.used / disk.total) > 0.85:
                status = HealthStatus.DEGRADED
                message = "System resources are elevated"
            else:
                status = HealthStatus.HEALTHY
                message = "System resources are normal"
            
            return HealthCheckResult(
                component="system",
                status=status,
                message=message,
                details=details
            )
        except Exception as e:
            return HealthCheckResult(
                component="system",
                status=HealthStatus.UNHEALTHY,
                message=f"Failed to check system health: {str(e)}"
            )
    
    def get_component_status(self, component_name: str) -> Optional[ComponentHealth]:
        """
        Get the health status of a specific component.
        
        Args:
            component_name: Name of the component
            
        Returns:
            Component health information or None if not found
        """
        return self._components.get(component_name)
    
    def get_all_components_status(self) -> Dict[str, ComponentHealth]:
        """
        Get the health status of all components.
        
        Returns:
            Dictionary of component names to health information
        """
        return self._components.copy()
    
    def get_health_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive health report.
        
        Returns:
            Health report dictionary
        """
        report = {
            "timestamp": datetime.now().isoformat(),
            "system": self.get_system_health().__dict__,
            "components": {},
            "overall_status": "healthy"
        }
        
        # Add component statuses
        unhealthy_components = []
        degraded_components = []
        
        for name, health in self._components.items():
            report["components"][name] = {
                "status": health.status.value,
                "last_check": health.last_check.isoformat(),
                "failure_count": health.failure_count,
                "consecutive_failures": health.consecutive_failures
            }
            
            if health.status == HealthStatus.UNHEALTHY:
                unhealthy_components.append(name)
            elif health.status == HealthStatus.DEGRADED:
                degraded_components.append(name)
        
        # Determine overall status
        if unhealthy_components:
            report["overall_status"] = "unhealthy"
        elif degraded_components:
            report["overall_status"] = "degraded"
        
        report["summary"] = {
            "total_components": len(self._components),
            "healthy_components": len(self._components) - len(unhealthy_components) - len(degraded_components),
            "degraded_components": len(degraded_components),
            "unhealthy_components": len(unhealthy_components)
        }
        
        return report
    
    async def start_monitoring(self, interval: int = 30) -> None:
        """
        Start periodic health monitoring.
        
        Args:
            interval: Monitoring interval in seconds
        """
        if self._running:
            return
            
        self._monitoring_interval = interval
        self._running = True
        self._monitoring_task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Health monitoring started with {interval}s interval")
    
    async def stop_monitoring(self) -> None:
        """Stop periodic health monitoring."""
        if not self._running:
            return
            
        self._running = False
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
        logger.info("Health monitoring stopped")
    
    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                # Check all component health
                await self.check_all_health()
                
                # Check system health
                system_result = self.get_system_health()
                if system_result.status != HealthStatus.HEALTHY:
                    logger.warning(f"System health: {system_result.status.value} - {system_result.message}")
                
                # Publish health report event periodically
                if int(time.time()) % (self._monitoring_interval * 2) == 0:
                    report = self.get_health_report()
                    event = Event(
                        event_type="health.report",
                        payload=report,
                        source="health_monitor"
                    )
                    await publish(event)
                
                # Wait for next check
                await asyncio.sleep(self._monitoring_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitoring loop: {str(e)}")
                await asyncio.sleep(self._monitoring_interval)


# Default health check functions
def database_health_check() -> HealthCheckResult:
    """Check database health."""
    try:
        # This would be implemented with actual database connection checks
        return HealthCheckResult(
            component="database",
            status=HealthStatus.HEALTHY,
            message="Database connection healthy"
        )
    except Exception as e:
        return HealthCheckResult(
            component="database",
            status=HealthStatus.UNHEALTHY,
            message=f"Database connection failed: {str(e)}"
        )


def api_health_check() -> HealthCheckResult:
    """Check API health."""
    try:
        # This would be implemented with actual API endpoint checks
        return HealthCheckResult(
            component="api",
            status=HealthStatus.HEALTHY,
            message="API endpoints responsive"
        )
    except Exception as e:
        return HealthCheckResult(
            component="api",
            status=HealthStatus.UNHEALTHY,
            message=f"API endpoints unavailable: {str(e)}"
        )


# Convenience functions
def get_health_monitor() -> HealthMonitor:
    """Get the global health monitor instance."""
    return HealthMonitor.get_instance()


async def start_health_monitoring(interval: int = 30) -> None:
    """Start health monitoring globally."""
    monitor = get_health_monitor()
    await monitor.start_monitoring(interval)


async def stop_health_monitoring() -> None:
    """Stop health monitoring globally."""
    monitor = get_health_monitor()
    await monitor.stop_monitoring()


def get_health_report() -> Dict[str, Any]:
    """Get a health report globally."""
    monitor = get_health_monitor()
    return monitor.get_health_report()
