"""
Core data types for the MCP (Model Context Protocol) implementation.

This module defines the data structures used in MCP communication according to
the Model Context Protocol specification.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
from enum import Enum
import uuid


class MessageType(Enum):
    """MCP message types."""
    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"


class RequestMethod(Enum):
    """MCP request methods."""
    # Core methods
    INITIALIZE = "initialize"
    PROMPT_LIST = "prompts/list"
    PROMPT_GET = "prompts/get"
    RESOURCE_LIST = "resources/list"
    RESOURCE_GET = "resources/get"
    RESOURCE_SUBSCRIBE = "resources/subscribe"
    RESOURCE_UNSUBSCRIBE = "resources/unsubscribe"
    TOOL_LIST = "tools/list"
    TOOL_CALL = "tools/call"
    
    # Additional methods
    NOTIFICATIONS_CAPABILITIES = "notifications/capabilities"
    NOTIFICATIONS_RESOURCES = "notifications/resources"
    NOTIFICATIONS_TOOLS = "notifications/tools"
    NOTIFICATIONS_PROMPTS = "notifications/prompts"
    
    # Ping method
    PING = "ping"


@dataclass
class MCPMessage:
    """Base MCP message structure."""
    
    # Message header
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: MessageType = MessageType.REQUEST
    timestamp: datetime = field(default_factory=datetime.now)
    
    # Protocol info
    protocol_version: str = "2024-01-01"
    
    def __post_init__(self):
        """Initialize timestamp if not provided."""
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)


@dataclass
class MCPRequest(MCPMessage):
    """MCP request message."""
    
    method: RequestMethod = RequestMethod.INITIALIZE
    params: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Initialize parent and set defaults."""
        super().__post_init__()
        self.type = MessageType.REQUEST
        if self.params is None:
            self.params = {}


@dataclass
class MCPResponse(MCPMessage):
    """MCP response message."""
    
    # For successful responses
    result: Optional[Dict[str, Any]] = None
    
    # For error responses
    error: Optional[Dict[str, Any]] = None
    
    # Reference to the request
    request_id: Optional[str] = None
    
    def __post_init__(self):
        """Initialize parent and set defaults."""
        super().__post_init__()
        self.type = MessageType.RESPONSE
        if self.result is None and self.error is None:
            self.result = {}


@dataclass
class Resource:
    """MCP resource representation."""
    
    uri: str
    name: str
    description: Optional[str] = None
    mime_type: str = "text/plain"
    size: Optional[int] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    etag: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Initialize timestamps if provided as strings."""
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        if isinstance(self.modified_at, str):
            self.modified_at = datetime.fromisoformat(self.modified_at)


@dataclass
class ResourceContents:
    """Contents of a resource."""
    
    uri: str
    contents: Union[str, bytes]
    mime_type: str = "text/plain"
    size: Optional[int] = None
    etag: Optional[str] = None


@dataclass
class Tool:
    """MCP tool representation."""
    
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Result from tool execution."""
    
    tool_name: str
    result: Any
    is_error: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Prompt:
    """MCP prompt representation."""
    
    name: str
    description: str
    arguments: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptArguments:
    """Arguments for a prompt."""
    
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptResult:
    """Result from prompt execution."""
    
    prompt_name: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Configuration:
    """MCP configuration settings."""
    
    max_resource_size: int = 1024 * 1024  # 1MB default
    max_message_size: int = 1024 * 1024  # 1MB default
    timeout_seconds: int = 30
    allowed_origins: List[str] = field(default_factory=list)
    enable_logging: bool = True
    log_level: str = "INFO"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClientCapabilities:
    """Capabilities supported by the MCP client."""
    
    prompts: bool = False
    resources: bool = False
    tools: bool = False
    notifications: bool = False


@dataclass
class ServerCapabilities:
    """Capabilities supported by the MCP server."""
    
    prompts: bool = True
    resources: bool = True
    tools: bool = True
    notifications: bool = True


@dataclass
class InitializeParams:
    """Parameters for initialize request."""
    
    protocol_version: str
    capabilities: ClientCapabilities
    client_info: Optional[Dict[str, str]] = None
    locale: str = "en-US"


@dataclass
class InitializeResult:
    """Result of initialize request."""
    
    protocol_version: str
    capabilities: ServerCapabilities
    server_info: Optional[Dict[str, str]] = None
