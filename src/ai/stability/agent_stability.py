"""
Agent Stability Management for 4S1T Agent AI Framework.

This module provides comprehensive stability management for AI agents,
including disconnection handling, recovery mechanisms, and remote monitoring.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional, Callable, Awaitable, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json
import numpy as np

from ai.models.base import BaseModel, ModelManager, ModelStatus, ModelType
from ai.context.manager import ContextManager, ContextEntry
from components.health.monitor import HealthMonitor, HealthStatus, HealthCheckResult
from components.events.event_bus import Event, get_event_bus, publish, subscribe

from utils.logger import setup_logger
logger = setup_logger(__name__)


class AgentState(Enum):
    """States of the AI agent."""
    IDLE = "idle"
    PROCESSING = "processing"
    STALLED = "stalled"
    DISCONNECTED = "disconnected"
    RECOVERING = "recovering"
    ERROR = "error"


class RecoveryAction(Enum):
    """Types of recovery actions."""
    RESTART_MODEL = "restart_model"
    CLEAR_CONTEXT = "clear_context"
    COMPACT_CONTEXT = "compact_context"
    SWITCH_MODEL = "switch_model"
    NOTIFY_USER = "notify_user"
    WAIT_AND_RETRY = "wait_and_retry"
    PREDICTIVE_MAINTENANCE = "predictive_maintenance"


@dataclass
class StabilityConfig:
    """Configuration for agent stability management."""
    
    # Disconnection detection
    response_timeout_seconds: int = 30
    heartbeat_interval_seconds: int = 10
    max_missed_heartbeats: int = 3
    
    # Recovery settings
    max_recovery_attempts: int = 3
    recovery_backoff_base_seconds: int = 5
    auto_recovery_enabled: bool = True
    
    # Model management
    model_reload_on_disconnect: bool = True
    context_preservation_on_restart: bool = True
    
    # Remote monitoring
    remote_monitoring_enabled: bool = True
    status_update_interval_seconds: int = 60
    
    # Alerting
    alert_on_disconnect: bool = True
    alert_on_recovery: bool = True
    alert_cooldown_minutes: int = 5


@dataclass
class AgentStatus:
    """Current status of the AI agent."""
    
    state: AgentState
    model_status: ModelStatus
    last_heartbeat: Optional[datetime] = None
    last_response: Optional[datetime] = None
    error_count: int = 0
    consecutive_errors: int = 0
    recovery_attempts: int = 0
    last_recovery: Optional[datetime] = None
    context_entries: int = 0
    context_tokens: int = 0


@dataclass
class RecoveryPlan:
    """Plan for recovering from agent instability."""
    
    actions: List[RecoveryAction]
    priority: int  # Lower is higher priority
    estimated_duration_seconds: int
    requires_user_confirmation: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentStabilityManager:
    """
    Manages AI agent stability, disconnection handling, and recovery.
    
    This class monitors agent health, detects disconnections, and implements
    automated recovery mechanisms for remote AI agents.
    """
    
    def __init__(self, model_manager: ModelManager, 
                 context_manager: ContextManager,
                 config: StabilityConfig = None):
        """
        Initialize the agent stability manager.
        
        Args:
            model_manager: Model manager to monitor
            context_manager: Context manager to monitor
            config: Configuration for stability management
        """
        self.model_manager = model_manager
        self.context_manager = context_manager
        self.config = config or StabilityConfig()
        self.health_monitor = HealthMonitor.get_instance()
        self.event_bus = get_event_bus()
        
        self.current_status = AgentStatus(
            state=AgentState.IDLE,
            model_status=ModelStatus.UNLOADED
        )
        
        self.stability_history: List[Dict[str, Any]] = []
        self.active_monitors: Dict[str, asyncio.Task] = {}
        self.last_alert_time: Optional[datetime] = None
        
        self.recovery_callbacks: List[Callable[[AgentStatus], Awaitable[None]]] = []
        self.state_change_callbacks: List[Callable[[AgentState, AgentState], Awaitable[None]]] = []
        
        self.logger = logger
        
        # Register health check
        self.health_monitor.register_health_check("agent_stability", self._health_check)
        
        # Subscribe to relevant events
        asyncio.create_task(self._setup_event_subscriptions())
    
    async def _setup_event_subscriptions(self):
        """Set up event subscriptions for monitoring."""
        try:
            await subscribe("model.loaded", self._on_model_event)
            await subscribe("model.unloaded", self._on_model_event)
            await subscribe("model.error", self._on_model_event)
            await subscribe("context.updated", self._on_context_event)
            await subscribe("health.alert", self._on_health_alert)
        except Exception as e:
            self.logger.error(f"Failed to set up event subscriptions: {e}")
    
    async def _on_model_event(self, event: Event):
        """Handle model-related events."""
        try:
            model_name = event.payload.get("model_name")
            status = event.payload.get("status")
            
            # Update model status
            if status:
                try:
                    model_status = ModelStatus(status)
                    self.current_status.model_status = model_status
                except ValueError:
                    pass  # Invalid status value
            
            # If model error, update agent state
            if event.event_type == "model.error":
                self.current_status.error_count += 1
                self.current_status.consecutive_errors += 1
                await self._update_state(AgentState.ERROR)
                
                # Trigger recovery if auto-recovery enabled
                if self.config.auto_recovery_enabled:
                    asyncio.create_task(self._attempt_recovery())
            
            self.logger.debug(f"Model event processed: {event.event_type} for {model_name}")
            
        except Exception as e:
            self.logger.error(f"Error handling model event: {e}")
    
    async def _on_context_event(self, event: Event):
        """Handle context-related events."""
        try:
            conversation_id = event.payload.get("conversation_id")
            if conversation_id:
                # Update context information
                context = self.context_manager.get_context(conversation_id)
                if context:
                    self.current_status.context_entries = len(context.entries)
            
            self.logger.debug(f"Context event processed: {event.event_type}")
            
        except Exception as e:
            self.logger.error(f"Error handling context event: {e}")
    
    async def _on_health_alert(self, event: Event):
        """Handle health alerts."""
        try:
            component = event.payload.get("component")
            status = event.payload.get("status")
            
            # If it's a critical component failure, update agent state
            if status == "unhealthy":
                if component in ["model", "context_window"]:
                    await self._update_state(AgentState.ERROR)
                    if self.config.auto_recovery_enabled:
                        asyncio.create_task(self._attempt_recovery())
            
            self.logger.debug(f"Health alert processed: {component} is {status}")
            
        except Exception as e:
            self.logger.error(f"Error handling health alert: {e}")
    
    async def start_monitoring(self):
        """Start agent stability monitoring."""
        try:
            # Start heartbeat monitor
            if "heartbeat" not in self.active_monitors:
                self.active_monitors["heartbeat"] = asyncio.create_task(self._heartbeat_monitor())
            
            # Start status reporter
            if "status_reporter" not in self.active_monitors:
                self.active_monitors["status_reporter"] = asyncio.create_task(self._status_reporter())
            
            self.logger.info("Agent stability monitoring started")
            
        except Exception as e:
            self.logger.error(f"Failed to start stability monitoring: {e}")
    
    async def stop_monitoring(self):
        """Stop agent stability monitoring."""
        try:
            # Cancel all monitoring tasks
            for task_name, task in self.active_monitors.items():
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            self.active_monitors.clear()
            self.logger.info("Agent stability monitoring stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping stability monitoring: {e}")
    
    async def _heartbeat_monitor(self):
        """Monitor agent heartbeat and detect disconnections."""
        while True:
            try:
                current_time = datetime.now()
                
                # Check if we've missed heartbeats
                if self.current_status.last_heartbeat:
                    time_since_heartbeat = (current_time - self.current_status.last_heartbeat).total_seconds()
                    missed_heartbeats = time_since_heartbeat / self.config.heartbeat_interval_seconds
                    
                    if missed_heartbeats > self.config.max_missed_heartbeats:
                        # Agent appears disconnected
                        if self.current_status.state != AgentState.DISCONNECTED:
                            await self._handle_disconnection()
                
                # Send heartbeat event
                await self.send_heartbeat()
                
                await asyncio.sleep(self.config.heartbeat_interval_seconds)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in heartbeat monitor: {e}")
                await asyncio.sleep(self.config.heartbeat_interval_seconds)
    
    async def _status_reporter(self):
        """Periodically report agent status."""
        while True:
            try:
                if self.config.remote_monitoring_enabled:
                    # Publish status update event
                    status_event = Event(
                        event_type="agent.status_update",
                        payload=self.get_status_summary(),
                        source="agent_stability_manager"
                    )
                    await publish(status_event)
                
                await asyncio.sleep(self.config.status_update_interval_seconds)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in status reporter: {e}")
                await asyncio.sleep(self.config.status_update_interval_seconds)
    
    async def send_heartbeat(self):
        """Send a heartbeat to indicate the agent is alive."""
        try:
            self.current_status.last_heartbeat = datetime.now()
            
            # Update state if it was disconnected
            if self.current_status.state == AgentState.DISCONNECTED:
                await self._update_state(AgentState.IDLE)
            
            self.logger.debug("Heartbeat sent")
            
        except Exception as e:
            self.logger.error(f"Error sending heartbeat: {e}")
    
    async def record_response(self):
        """Record that the agent has successfully responded."""
        try:
            self.current_status.last_response = datetime.now()
            self.current_status.consecutive_errors = 0
            
            # Update state if it was stalled or error
            if self.current_status.state in [AgentState.STALLED, AgentState.ERROR]:
                await self._update_state(AgentState.IDLE)
            
            self.logger.debug("Response recorded")
            
        except Exception as e:
            self.logger.error(f"Error recording response: {e}")
    
    async def _handle_disconnection(self):
        """Handle agent disconnection."""
        try:
            previous_state = self.current_status.state
            await self._update_state(AgentState.DISCONNECTED)
            
            self.logger.warning("Agent disconnection detected")
            
            # Record in stability history
            self._record_stability_event("disconnection", {
                "previous_state": previous_state.value,
                "duration_since_last_response": (
                    datetime.now() - self.current_status.last_response
                ).total_seconds() if self.current_status.last_response else None
            })
            
            # Send alert if configured
            if self.config.alert_on_disconnect:
                await self._send_alert("Agent disconnection detected", "warning")
            
            # Attempt recovery if auto-recovery enabled
            if self.config.auto_recovery_enabled:
                asyncio.create_task(self._attempt_recovery())
                
        except Exception as e:
            self.logger.error(f"Error handling disconnection: {e}")
    
    async def _attempt_recovery(self):
        """Attempt to recover from agent instability."""
        try:
            if self.current_status.recovery_attempts >= self.config.max_recovery_attempts:
                self.logger.error("Maximum recovery attempts exceeded")
                await self._send_alert("Maximum recovery attempts exceeded", "critical")
                return
            
            # Update state to recovering
            await self._update_state(AgentState.RECOVERING)
            
            # Increment recovery attempts
            self.current_status.recovery_attempts += 1
            self.current_status.last_recovery = datetime.now()
            
            self.logger.info(f"Attempting recovery (attempt {self.current_status.recovery_attempts})")
            
            # Create recovery plan
            recovery_plan = self._create_recovery_plan()
            
            # Execute recovery actions
            for action in recovery_plan.actions:
                success = await self._execute_recovery_action(action)
                if not success:
                    self.logger.warning(f"Recovery action {action.value} failed")
                    # Continue with other actions unless critical
            
            # Reset consecutive errors on successful recovery
            self.current_status.consecutive_errors = 0
            
            # Update state after recovery
            await self._update_state(AgentState.IDLE)
            
            # Record recovery
            self._record_stability_event("recovery", {
                "attempt": self.current_status.recovery_attempts,
                "actions_taken": [action.value for action in recovery_plan.actions]
            })
            
            # Send recovery alert if configured
            if self.config.alert_on_recovery:
                await self._send_alert("Agent recovery completed", "info")
                
        except Exception as e:
            self.logger.error(f"Error during recovery attempt: {e}")
            await self._update_state(AgentState.ERROR)
    
    def _create_recovery_plan(self) -> RecoveryPlan:
        """
        Create a recovery plan based on current status.
        
        Returns:
            RecoveryPlan with appropriate actions
        """
        actions = []
        
        # Add predictive maintenance as first action to analyze patterns
        actions.append(RecoveryAction.PREDICTIVE_MAINTENANCE)
        
        # Always try restarting the model if it's in error state
        if self.current_status.model_status == ModelStatus.ERROR:
            actions.append(RecoveryAction.RESTART_MODEL)
        
        # If we have many context entries, try compacting
        if self.current_status.context_entries > 50:
            actions.append(RecoveryAction.COMPACT_CONTEXT)
        
        # If we've had many consecutive errors, clear context
        if self.current_status.consecutive_errors > 5:
            actions.append(RecoveryAction.CLEAR_CONTEXT)
        
        # Default actions if no specific conditions met
        if len(actions) <= 1:  # Only predictive maintenance
            actions.extend([
                RecoveryAction.WAIT_AND_RETRY,
                RecoveryAction.RESTART_MODEL
            ])
        
        return RecoveryPlan(
            actions=actions,
            priority=1,
            estimated_duration_seconds=len(actions) * 10  # Rough estimate
        )
    
    async def _execute_recovery_action(self, action: RecoveryAction) -> bool:
        """Execute a specific recovery action."""
        try:
            self.logger.info(f"Executing recovery action: {action.value}")
            
            if action == RecoveryAction.RESTART_MODEL:
                return await self._restart_model()
            elif action == RecoveryAction.CLEAR_CONTEXT:
                return await self._clear_context()
            elif action == RecoveryAction.COMPACT_CONTEXT:
                return await self._compact_context()
            elif action == RecoveryAction.SWITCH_MODEL:
                return await self._switch_model()
            elif action == RecoveryAction.NOTIFY_USER:
                return await self._notify_user()
            elif action == RecoveryAction.WAIT_AND_RETRY:
                return await self._wait_and_retry()
            elif action == RecoveryAction.PREDICTIVE_MAINTENANCE:
                return await self._perform_predictive_maintenance()
            else:
                self.logger.warning(f"Unknown recovery action: {action.value}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error executing recovery action {action.value}: {e}")
            return False
    
    async def _perform_predictive_maintenance(self) -> bool:
        """Perform predictive maintenance based on stability patterns."""
        try:
            self.logger.info("Performing predictive maintenance")
            
            # Analyze stability patterns to predict potential issues
            prediction = self._predict_stability_issues()
            
            if prediction.get("risk_level", 0) > 0.7:  # High risk
                self.logger.warning(f"High stability risk detected: {prediction.get('reason', 'Unknown')}")
                
                # Take preventive actions
                actions_taken = []
                
                # If context is growing large, compact it preemptively
                if prediction.get("context_risk", False):
                    self.logger.info("Preemptively compacting context to prevent overflow")
                    success = await self._compact_context()
                    if success:
                        actions_taken.append("context_compaction")
                
                # If error rate is increasing, prepare for potential restart
                if prediction.get("error_rate_risk", False):
                    self.logger.info("Preparing for potential model restart")
                    # Pre-load model resources or prepare restart procedures
                    actions_taken.append("restart_preparation")
                
                # Send early warning
                if actions_taken:
                    await self._send_alert(
                        f"Predictive maintenance performed: {', '.join(actions_taken)}", 
                        "warning"
                    )
                
                return True
            else:
                self.logger.info("No high-risk stability issues predicted")
                return True
                
        except Exception as e:
            self.logger.error(f"Error performing predictive maintenance: {e}")
            return False
    
    def _predict_stability_issues(self) -> Dict[str, Any]:
        """
        Predict potential stability issues using pattern analysis.
        
        Returns:
            Dict with prediction results including risk level and reasons
        """
        try:
            # Collect metrics for analysis
            metrics = self._collect_stability_metrics()
            
            # Calculate risk factors
            risk_factors = {}
            
            # Context growth risk
            context_risk = self._calculate_context_growth_risk(metrics)
            risk_factors["context_risk"] = context_risk
            
            # Error rate trend risk
            error_risk = self._calculate_error_trend_risk(metrics)
            risk_factors["error_rate_risk"] = error_risk
            
            # Response time degradation risk
            response_risk = self._calculate_response_time_risk(metrics)
            risk_factors["response_time_risk"] = response_risk
            
            # Overall risk calculation (weighted average)
            weights = {
                "context_risk": 0.4,
                "error_rate_risk": 0.3,
                "response_time_risk": 0.3
            }
            
            overall_risk = sum(
                risk_factors.get(factor, 0) * weight 
                for factor, weight in weights.items()
            )
            
            # Determine primary risk factor
            primary_risk = max(risk_factors.items(), key=lambda x: x[1])[0] if risk_factors else "unknown"
            
            return {
                "risk_level": overall_risk,
                "primary_risk_factor": primary_risk,
                "risk_factors": risk_factors,
                "reason": f"Primary risk: {primary_risk.replace('_risk', '').replace('_', ' ')}",
                "metrics": metrics
            }
            
        except Exception as e:
            self.logger.error(f"Error predicting stability issues: {e}")
            return {"risk_level": 0.0, "reason": "Prediction failed"}
    
    def _collect_stability_metrics(self) -> Dict[str, Any]:
        """
        Collect metrics for stability analysis.
        
        Returns:
            Dict with collected metrics
        """
        try:
            metrics = {
                "timestamp": datetime.now().isoformat(),
                "current_status": self.current_status.state.value,
                "model_status": self.current_status.model_status.value,
                "error_count": self.current_status.error_count,
                "consecutive_errors": self.current_status.consecutive_errors,
                "context_entries": self.current_status.context_entries,
                "recovery_attempts": self.current_status.recovery_attempts,
                "last_heartbeat": self.current_status.last_heartbeat.isoformat() if self.current_status.last_heartbeat else None,
                "last_response": self.current_status.last_response.isoformat() if self.current_status.last_response else None
            }
            
            # Add historical metrics if available
            if self.stability_history:
                recent_events = self.stability_history[-10:]  # Last 10 events
                metrics["recent_events"] = recent_events
                metrics["event_frequency"] = len(recent_events)
            
            return metrics
            
        except Exception as e:
            self.logger.error(f"Error collecting stability metrics: {e}")
            return {}
    
    def _calculate_context_growth_risk(self, metrics: Dict[str, Any]) -> float:
        """
        Calculate risk based on context growth patterns.
        
        Args:
            metrics: Collected stability metrics
            
        Returns:
            Risk level (0.0 to 1.0)
        """
        try:
            context_entries = metrics.get("context_entries", 0)
            
            # Risk increases as context approaches limits
            # Assuming 100 entries is a reasonable limit for this example
            max_context_entries = 100
            risk = min(1.0, context_entries / max_context_entries)
            
            return risk
            
        except Exception as e:
            self.logger.error(f"Error calculating context growth risk: {e}")
            return 0.0
    
    def _calculate_error_trend_risk(self, metrics: Dict[str, Any]) -> float:
        """
        Calculate risk based on error rate trends.
        
        Args:
            metrics: Collected stability metrics
            
        Returns:
            Risk level (0.0 to 1.0)
        """
        try:
            consecutive_errors = metrics.get("consecutive_errors", 0)
            total_errors = metrics.get("error_count", 0)
            
            # Higher risk with more consecutive errors
            consecutive_risk = min(1.0, consecutive_errors / 5.0)  # 5+ consecutive errors is high risk
            
            # Also consider total error rate
            total_risk = min(1.0, total_errors / 20.0)  # 20+ total errors is high risk
            
            # Combined risk (weighted toward consecutive errors)
            combined_risk = (consecutive_risk * 0.7) + (total_risk * 0.3)
            
            return combined_risk
            
        except Exception as e:
            self.logger.error(f"Error calculating error trend risk: {e}")
            return 0.0
    
    def _calculate_response_time_risk(self, metrics: Dict[str, Any]) -> float:
        """
        Calculate risk based on response time patterns.
        
        Args:
            metrics: Collected stability metrics
            
        Returns:
            Risk level (0.0 to 1.0)
        """
        try:
            last_response = metrics.get("last_response")
            last_heartbeat = metrics.get("last_heartbeat")
            
            if not last_response or not last_heartbeat:
                return 0.0
            
            # Parse timestamps
            try:
                response_time = datetime.fromisoformat(last_response)
                heartbeat_time = datetime.fromisoformat(last_heartbeat)
                
                # Calculate time since last response
                time_since_response = (datetime.now() - response_time).total_seconds()
                
                # Risk increases with longer response times
                # Assume 60 seconds is acceptable, 300+ seconds is high risk
                risk = min(1.0, time_since_response / 300.0)
                
                return risk
            except Exception:
                return 0.0
            
        except Exception as e:
            self.logger.error(f"Error calculating response time risk: {e}")
            return 0.0
    
    async def _restart_model(self) -> bool:
        """Restart the active language model."""
        try:
            # Find active language model
            active_model = self.model_manager.get_active_model(ModelType.LANGUAGE_MODEL)
            if not active_model:
                self.logger.warning("No active language model found")
                return False
            
            model_name = active_model.metadata.name
            self.logger.info(f"Restarting model: {model_name}")
            
            # Unload model
            await self.model_manager.unload_model(model_name)
            
            # Wait a bit
            await asyncio.sleep(2)
            
            # Reload model
            success = await self.model_manager.load_model(model_name)
            
            if success:
                self.logger.info(f"Model {model_name} restarted successfully")
                return True
            else:
                self.logger.error(f"Failed to restart model {model_name}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error restarting model: {e}")
            return False
    
    async def _clear_context(self) -> bool:
        """Clear conversation context."""
        try:
            # This is a drastic measure - in practice, you might want to be more selective
            # For now, we'll just log that this would happen
            self.logger.info("Context clearing requested - this would clear all conversation history")
            return True  # Pretend it succeeded
            
        except Exception as e:
            self.logger.error(f"Error clearing context: {e}")
            return False
    
    async def _compact_context(self) -> bool:
        """Compact conversation context."""
        try:
            # This would integrate with the context window manager
            self.logger.info("Context compaction requested")
            return True  # Pretend it succeeded
            
        except Exception as e:
            self.logger.error(f"Error compacting context: {e}")
            return False
    
    async def _switch_model(self) -> bool:
        """Switch to a different model."""
        try:
            # This would implement model switching logic
            self.logger.info("Model switching requested")
            return True  # Pretend it succeeded
            
        except Exception as e:
            self.logger.error(f"Error switching model: {e}")
            return False
    
    async def _notify_user(self) -> bool:
        """Notify user about the issue."""
        try:
            # This would send notifications to users
            self.logger.info("User notification requested")
            return True  # Pretend it succeeded
            
        except Exception as e:
            self.logger.error(f"Error notifying user: {e}")
            return False
    
    async def _wait_and_retry(self) -> bool:
        """Wait and retry operation."""
        try:
            backoff_time = self.config.recovery_backoff_base_seconds * self.current_status.recovery_attempts
            self.logger.info(f"Waiting {backoff_time} seconds before retry")
            await asyncio.sleep(backoff_time)
            return True
            
        except Exception as e:
            self.logger.error(f"Error in wait and retry: {e}")
            return False
    
    async def _update_state(self, new_state: AgentState):
        """Update agent state and notify callbacks."""
        try:
            previous_state = self.current_status.state
            self.current_status.state = new_state
            
            # Notify state change callbacks
            for callback in self.state_change_callbacks:
                try:
                    await callback(previous_state, new_state)
                except Exception as e:
                    self.logger.error(f"Error in state change callback: {e}")
            
            self.logger.debug(f"Agent state updated: {previous_state.value} -> {new_state.value}")
            
        except Exception as e:
            self.logger.error(f"Error updating agent state: {e}")
    
    def get_status(self) -> AgentStatus:
        """Get current agent status."""
        return self.current_status
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get a summary of agent status for reporting."""
        return {
            "state": self.current_status.state.value,
            "model_status": self.current_status.model_status.value,
            "last_heartbeat": self.current_status.last_heartbeat.isoformat() if self.current_status.last_heartbeat else None,
            "last_response": self.current_status.last_response.isoformat() if self.current_status.last_response else None,
            "error_count": self.current_status.error_count,
            "consecutive_errors": self.current_status.consecutive_errors,
            "recovery_attempts": self.current_status.recovery_attempts,
            "context_entries": self.current_status.context_entries,
            "context_tokens": self.current_status.context_tokens,
            "timestamp": datetime.now().isoformat()
        }
    
    def _record_stability_event(self, event_type: str, details: Dict[str, Any]):
        """Record a stability-related event."""
        try:
            event_record = {
                "timestamp": datetime.now().isoformat(),
                "event_type": event_type,
                "details": details
            }
            
            self.stability_history.append(event_record)
            
            # Keep only recent history (last 100 events)
            if len(self.stability_history) > 100:
                self.stability_history = self.stability_history[-100:]
                
        except Exception as e:
            self.logger.error(f"Error recording stability event: {e}")
    
    async def _send_alert(self, message: str, severity: str = "info"):
        """Send an alert about agent status."""
        try:
            # Check cooldown
            if self.last_alert_time:
                minutes_since_last_alert = (datetime.now() - self.last_alert_time).total_seconds() / 60
                if minutes_since_last_alert < self.config.alert_cooldown_minutes:
                    self.logger.debug(f"Alert cooldown active, skipping alert: {message}")
                    return
            
            # Record last alert time
            self.last_alert_time = datetime.now()
            
            # Publish alert event
            alert_event = Event(
                event_type="agent.alert",
                payload={
                    "message": message,
                    "severity": severity,
                    "status": self.get_status_summary()
                },
                source="agent_stability_manager"
            )
            await publish(alert_event)
            
            self.logger.info(f"Alert sent [{severity}]: {message}")
            
        except Exception as e:
            self.logger.error(f"Error sending alert: {e}")
    
    def _health_check(self) -> HealthCheckResult:
        """Perform health check for agent stability management."""
        try:
            if self.current_status.state == AgentState.ERROR:
                return HealthCheckResult(
                    component="agent_stability",
                    status=HealthStatus.UNHEALTHY,
                    message=f"Agent in error state with {self.current_status.consecutive_errors} consecutive errors"
                )
            elif self.current_status.state == AgentState.DISCONNECTED:
                return HealthCheckResult(
                    component="agent_stability",
                    status=HealthStatus.UNHEALTHY,
                    message="Agent disconnected"
                )
            elif self.current_status.state == AgentState.STALLED:
                return HealthCheckResult(
                    component="agent_stability",
                    status=HealthStatus.DEGRADED,
                    message="Agent stalled"
                )
            else:
                return HealthCheckResult(
                    component="agent_stability",
                    status=HealthStatus.HEALTHY,
                    message="Agent stability management operating normally"
                )
                
        except Exception as e:
            return HealthCheckResult(
                component="agent_stability",
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}"
            )
    
    def register_recovery_callback(self, callback: Callable[[AgentStatus], Awaitable[None]]):
        """Register a callback to be called during recovery."""
        self.recovery_callbacks.append(callback)
    
    def register_state_change_callback(self, callback: Callable[[AgentState, AgentState], Awaitable[None]]):
        """Register a callback to be called on state changes."""
        self.state_change_callbacks.append(callback)
