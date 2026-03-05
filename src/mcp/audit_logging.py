"""
Resource access logging for the MCP (Model Context Protocol) implementation.

This module provides audit logging for resource access and tool execution
to track usage and security events.
"""

import logging
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from pathlib import Path
import os


logger = logging.getLogger(__name__)


@dataclass
class ResourceAccessLogEntry:
    """Log entry for resource access events."""
    
    timestamp: str
    client_id: str
    resource_uri: str
    operation: str  # GET, LIST, SUBSCRIBE, UNSUBSCRIBE, etc.
    success: bool
    error_message: Optional[str] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    execution_time_ms: Optional[float] = None
    request_size_bytes: Optional[int] = None
    response_size_bytes: Optional[int] = None
    authentication_method: Optional[str] = None
    permissions_checked: Optional[List[str]] = None


@dataclass
class ToolExecutionLogEntry:
    """Log entry for tool execution events."""
    
    timestamp: str
    client_id: str
    tool_name: str
    success: bool
    execution_time_ms: float
    error_message: Optional[str] = None
    input_args: Optional[Dict[str, Any]] = None
    output_result: Optional[Any] = None
    timed_out: bool = False
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    resource_usage: Optional[Dict[str, Any]] = None  # CPU, memory, etc.
    sandbox_limits: Optional[Dict[str, Any]] = None
    authentication_method: Optional[str] = None
    permissions_checked: Optional[List[str]] = None


@dataclass
class NotificationLogEntry:
    """Log entry for notification events."""
    
    timestamp: str
    client_id: str
    notification_type: str  # resources, tools, prompts
    event_type: str  # sent, received, failed
    success: bool
    error_message: Optional[str] = None
    payload_size_bytes: Optional[int] = None
    delivery_time_ms: Optional[float] = None
    retry_count: int = 0
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None


class MCPAuditLogger:
    """Audit logger for MCP resource access and tool execution."""
    
    def __init__(self, log_file_path: Optional[str] = None, max_file_size_mb: int = 10, backup_count: int = 5):
        """
        Initialize the audit logger.
        
        Args:
            log_file_path: Path to the log file (optional, defaults to mcp_audit.log)
            max_file_size_mb: Maximum log file size before rotation (default 10MB)
            backup_count: Number of backup files to keep (default 5)
        """
        self.log_file_path = log_file_path or "logs/mcp_audit.log"
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self.backup_count = backup_count
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        
        # Set up file logging with rotation
        self._setup_file_logging()
    
    def _setup_file_logging(self):
        """Set up file logging with rotation for audit entries."""
        try:
            # Create logs directory if it doesn't exist
            log_path = Path(self.log_file_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Import RotatingFileHandler
            from logging.handlers import RotatingFileHandler
            
            # Set up rotating file handler
            file_handler = RotatingFileHandler(
                self.log_file_path,
                maxBytes=self.max_file_size_bytes,
                backupCount=self.backup_count
            )
            file_handler.setLevel(logging.INFO)
            
            # Create formatter
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            
            # Add handler to logger
            self.logger.addHandler(file_handler)
            self.logger.setLevel(logging.INFO)
            
        except ImportError:
            # Fallback to regular file handler if RotatingFileHandler is not available
            self.logger.warning("RotatingFileHandler not available, using regular FileHandler")
            file_handler = logging.FileHandler(self.log_file_path)
            file_handler.setLevel(logging.INFO)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            self.logger.setLevel(logging.INFO)
    
    def log_resource_access(self, entry: ResourceAccessLogEntry):
        """
        Log a resource access event.
        
        Args:
            entry: Resource access log entry
        """
        try:
            # Log to file with appropriate level
            if entry.success:
                log_level = logging.INFO
            else:
                log_level = logging.WARNING if entry.error_message else logging.ERROR
            
            # Create detailed log message
            log_message = (
                f"RESOURCE_ACCESS - Client: {entry.client_id} - "
                f"Resource: {entry.resource_uri} - "
                f"Operation: {entry.operation} - "
                f"Success: {entry.success}"
            )
            
            if entry.error_message:
                log_message += f" - Error: {entry.error_message}"
            
            if entry.execution_time_ms:
                log_message += f" - Time: {entry.execution_time_ms:.2f}ms"
            
            if entry.response_size_bytes:
                log_message += f" - Size: {entry.response_size_bytes} bytes"
            
            self.logger.log(log_level, log_message)
            
            # Also log structured data for machine parsing
            structured_entry = asdict(entry)
            self.logger.log(log_level, f"RESOURCE_ACCESS_JSON: {json.dumps(structured_entry)}")
            
        except Exception as e:
            self.logger.error(f"Failed to log resource access: {e}")
    
    def log_tool_execution(self, entry: ToolExecutionLogEntry):
        """
        Log a tool execution event.
        
        Args:
            entry: Tool execution log entry
        """
        try:
            # Log to file with appropriate level
            if entry.success:
                log_level = logging.INFO
            else:
                log_level = logging.WARNING if entry.timed_out else logging.ERROR
            
            # Create detailed log message
            log_message = (
                f"TOOL_EXECUTION - Client: {entry.client_id} - "
                f"Tool: {entry.tool_name} - "
                f"Success: {entry.success}"
            )
            
            if entry.error_message:
                log_message += f" - Error: {entry.error_message}"
            
            log_message += f" - Time: {entry.execution_time_ms:.2f}ms"
            
            if entry.resource_usage:
                cpu_percent = entry.resource_usage.get('cpu_percent', 'N/A')
                memory_mb = entry.resource_usage.get('memory_mb', 'N/A')
                log_message += f" - Resources: CPU {cpu_percent}%, Mem {memory_mb}MB"
            
            self.logger.log(log_level, log_message)
            
            # Also log structured data
            structured_entry = asdict(entry)
            # Sanitize sensitive data from structured logging
            if structured_entry.get('input_args'):
                # In a production environment, you might want to sanitize sensitive args
                # For now, we'll keep them but note they exist
                pass
            
            self.logger.log(log_level, f"TOOL_EXECUTION_JSON: {json.dumps(structured_entry)}")
            
        except Exception as e:
            self.logger.error(f"Failed to log tool execution: {e}")
    
    def log_notification(self, entry: NotificationLogEntry):
        """
        Log a notification event.
        
        Args:
            entry: Notification log entry
        """
        try:
            # Log to file with appropriate level
            if entry.success:
                log_level = logging.INFO
            else:
                log_level = logging.WARNING if entry.retry_count > 0 else logging.ERROR
            
            # Create detailed log message
            log_message = (
                f"NOTIFICATION - Client: {entry.client_id} - "
                f"Type: {entry.notification_type} - "
                f"Event: {entry.event_type} - "
                f"Success: {entry.success}"
            )
            
            if entry.error_message:
                log_message += f" - Error: {entry.error_message}"
            
            if entry.delivery_time_ms:
                log_message += f" - Delivery Time: {entry.delivery_time_ms:.2f}ms"
            
            if entry.retry_count > 0:
                log_message += f" - Retries: {entry.retry_count}"
            
            self.logger.log(log_level, log_message)
            
            # Also log structured data
            structured_entry = asdict(entry)
            self.logger.log(log_level, f"NOTIFICATION_JSON: {json.dumps(structured_entry)}")
            
        except Exception as e:
            self.logger.error(f"Failed to log notification: {e}")
    
    def get_log_entries(self, limit: int = 100, filter_type: Optional[str] = None) -> list:
        """
        Retrieve recent log entries (for debugging/admin purposes).
        
        Args:
            limit: Maximum number of entries to retrieve
            filter_type: Filter by entry type ('resource', 'tool', 'notification', or None for all)
            
        Returns:
            list: Recent log entries
        """
        try:
            if not Path(self.log_file_path).exists():
                return []
            
            entries = []
            with open(self.log_file_path, 'r') as f:
                lines = f.readlines()
                # Get the most recent entries
                relevant_lines = lines[-limit:] if len(lines) > limit else lines
                
                # Filter by type if specified
                if filter_type:
                    if filter_type == 'resource':
                        relevant_lines = [line for line in relevant_lines if 'RESOURCE_ACCESS' in line]
                    elif filter_type == 'tool':
                        relevant_lines = [line for line in relevant_lines if 'TOOL_EXECUTION' in line]
                    elif filter_type == 'notification':
                        relevant_lines = [line for line in relevant_lines if 'NOTIFICATION' in line]
                
                for line in relevant_lines:
                    entries.append(line.strip())
            
            return entries
        except Exception as e:
            self.logger.error(f"Failed to read log entries: {e}")
            return []
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about log entries.
        
        Returns:
            Dict with statistics about resource access, tool execution, and notifications
        """
        try:
            if not Path(self.log_file_path).exists():
                return {
                    "total_entries": 0,
                    "resource_access": {"total": 0, "successful": 0, "failed": 0},
                    "tool_execution": {"total": 0, "successful": 0, "failed": 0, "timeouts": 0},
                    "notifications": {"total": 0, "successful": 0, "failed": 0, "retries": 0}
                }
            
            stats = {
                "total_entries": 0,
                "resource_access": {"total": 0, "successful": 0, "failed": 0},
                "tool_execution": {"total": 0, "successful": 0, "failed": 0, "timeouts": 0},
                "notifications": {"total": 0, "successful": 0, "failed": 0, "retries": 0}
            }
            
            with open(self.log_file_path, 'r') as f:
                for line in f:
                    stats["total_entries"] += 1
                    
                    if 'RESOURCE_ACCESS' in line:
                        stats["resource_access"]["total"] += 1
                        if 'Success: True' in line:
                            stats["resource_access"]["successful"] += 1
                        else:
                            stats["resource_access"]["failed"] += 1
                    
                    elif 'TOOL_EXECUTION' in line:
                        stats["tool_execution"]["total"] += 1
                        if 'Success: True' in line:
                            stats["tool_execution"]["successful"] += 1
                        else:
                            stats["tool_execution"]["failed"] += 1
                        if 'timed_out: True' in line:
                            stats["tool_execution"]["timeouts"] += 1
                    
                    elif 'NOTIFICATION' in line:
                        stats["notifications"]["total"] += 1
                        if 'Success: True' in line:
                            stats["notifications"]["successful"] += 1
                        else:
                            stats["notifications"]["failed"] += 1
                        if 'Retries:' in line:
                            stats["notifications"]["retries"] += 1
            
            return stats
        except Exception as e:
            self.logger.error(f"Failed to get log statistics: {e}")
            return {"error": str(e)}


# Example usage
if __name__ == "__main__":
    # Configure root logger
    logging.basicConfig(level=logging.INFO)
    
    # Create audit logger
    audit_logger = MCPAuditLogger("logs/test_mcp_audit.log")
    
    # Log a resource access event
    resource_entry = ResourceAccessLogEntry(
        timestamp=datetime.now().isoformat(),
        client_id="test_client_123",
        resource_uri="file:///example.txt",
        operation="GET",
        success=True,
        execution_time_ms=15.5,
        response_size_bytes=1024,
        authentication_method="token"
    )
    
    audit_logger.log_resource_access(resource_entry)
    
    # Log a tool execution event
    tool_entry = ToolExecutionLogEntry(
        timestamp=datetime.now().isoformat(),
        client_id="test_client_123",
        tool_name="calculator",
        success=True,
        execution_time_ms=25.3,
        input_args={"operation": "add", "a": 5, "b": 3},
        output_result={"result": 8},
        resource_usage={"cpu_percent": 15.2, "memory_mb": 50}
    )
    
    audit_logger.log_tool_execution(tool_entry)
    
    # Log a notification event
    notification_entry = NotificationLogEntry(
        timestamp=datetime.now().isoformat(),
        client_id="test_client_123",
        notification_type="resources",
        event_type="sent",
        success=True,
        delivery_time_ms=5.2,
        payload_size_bytes=256
    )
    
    audit_logger.log_notification(notification_entry)
    
    # Retrieve log entries
    entries = audit_logger.get_log_entries(10)
    print("Recent log entries:")
    for entry in entries:
        print(f"  {entry}")
    
    # Get statistics
    stats = audit_logger.get_statistics()
    print("\nLog Statistics:")
    print(f"  Total entries: {stats['total_entries']}")
    print(f"  Resource access: {stats['resource_access']}")
    print(f"  Tool execution: {stats['tool_execution']}")
    print(f"  Notifications: {stats['notifications']}")

