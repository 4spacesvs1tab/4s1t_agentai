"""
MCP (Model Context Protocol) server implementation for the 4S1T Agent AI framework.

This module implements the Model Context Protocol server according to the
specification at https://modelcontextprotocol.io
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Callable, Awaitable
from datetime import datetime

from .mcp_types import (
    MCPRequest, MCPResponse, RequestMethod, Resource, Tool, Prompt,
    Configuration, ServerCapabilities, ClientCapabilities,
    InitializeParams, InitializeResult, ResourceContents,
    ToolResult, PromptResult, PromptArguments
)
from .security import AuthenticationManager, RateLimiter, ClientIdentity
from .sandbox import ToolSandbox, SandboxConfig
from .audit_logging import MCPAuditLogger, ResourceAccessLogEntry, ToolExecutionLogEntry, NotificationLogEntry
from .context_serialization import serialize_context, deserialize_context, compress_context, decompress_context
from .tool_framework import ToolRegistry, ToolExecutor, ToolRegistration, ToolMetadata
from .tool_result_processing import ToolResultProcessor, ProcessedToolResult

logger = logging.getLogger(__name__)


class MCPServer:
    """
    MCP (Model Context Protocol) server implementation.
    
    This server handles MCP protocol requests and provides access to resources,
    tools, and prompts as defined in the Model Context Protocol specification.
    """
    
    def __init__(self, config: Optional[Configuration] = None):
        """
        Initialize the MCP server.
        
        Args:
            config: Server configuration
        """
        self.config = config or Configuration()
        self.capabilities = ServerCapabilities()
        self.resources: Dict[str, Resource] = {}
        self.tools: Dict[str, Tool] = {}
        self.prompts: Dict[str, Prompt] = {}
        self.clients: Dict[str, Dict[str, Any]] = {}
        self.authenticated_clients: Dict[str, ClientIdentity] = {}
        self.request_handlers: Dict[RequestMethod, Callable] = {}
        self.tool_executors: Dict[str, Callable] = {}
        self.running = False
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        
        # Resource caching
        self.resource_cache: Dict[str, Dict[str, Any]] = {}  # uri -> {content, timestamp, ttl}
        self.cache_ttl_seconds = 300  # 5 minutes default TTL
        
        # Security components
        self.auth_manager = AuthenticationManager()
        self.rate_limiter = RateLimiter(requests_per_minute=60)  # Default 60 RPM
        
        # Tool execution sandbox
        sandbox_config = SandboxConfig(
            timeout_seconds=self.config.timeout_seconds,
            memory_limit_mb=100,
            max_concurrent_executions=10
        )
        self.tool_sandbox = ToolSandbox(sandbox_config)
        
        # Tool framework components
        self.tool_registry = ToolRegistry()
        self.tool_executor = ToolExecutor(self.tool_registry)
        
        # Tool result processing
        self.tool_result_processor = ToolResultProcessor()
        
        # Audit logging — use absolute path so it works regardless of WORKDIR
        import os as _os
        _log_path = _os.environ.get("MCP_AUDIT_LOG", "/app/logs/mcp_audit.log")
        self.audit_logger = MCPAuditLogger(_log_path)
        
        # Register default handlers
        self._register_default_handlers()
    
    def _register_default_handlers(self):
        """Register default request handlers."""
        self.request_handlers[RequestMethod.INITIALIZE] = self._handle_initialize
        self.request_handlers[RequestMethod.PROMPT_LIST] = self._handle_prompt_list
        self.request_handlers[RequestMethod.PROMPT_GET] = self._handle_prompt_get
        self.request_handlers[RequestMethod.RESOURCE_LIST] = self._handle_resource_list
        self.request_handlers[RequestMethod.RESOURCE_GET] = self._handle_resource_get
        self.request_handlers[RequestMethod.RESOURCE_SUBSCRIBE] = self._handle_resource_subscribe
        self.request_handlers[RequestMethod.RESOURCE_UNSUBSCRIBE] = self._handle_resource_unsubscribe
        self.request_handlers[RequestMethod.TOOL_LIST] = self._handle_tool_list
        self.request_handlers[RequestMethod.TOOL_CALL] = self._handle_tool_call
        self.request_handlers[RequestMethod.PING] = self._handle_ping
        
        # Notification capability handlers
        self.request_handlers[RequestMethod.NOTIFICATIONS_CAPABILITIES] = self._handle_notifications_capabilities
        self.request_handlers[RequestMethod.NOTIFICATIONS_RESOURCES] = self._handle_notifications_resources
        self.request_handlers[RequestMethod.NOTIFICATIONS_TOOLS] = self._handle_notifications_tools
        self.request_handlers[RequestMethod.NOTIFICATIONS_PROMPTS] = self._handle_notifications_prompts
    
    async def start(self) -> bool:
        """
        Start the MCP server.
        
        Returns:
            bool: True if server started successfully, False otherwise
        """
        try:
            self.running = True
            self.logger.info("MCP server started")
            return True
        except Exception as e:
            self.logger.error(f"Failed to start MCP server: {e}")
            return False
    
    async def stop(self) -> bool:
        """
        Stop the MCP server.
        
        Returns:
            bool: True if server stopped successfully, False otherwise
        """
        try:
            self.running = False
            self.logger.info("MCP server stopped")
            return True
        except Exception as e:
            self.logger.error(f"Failed to stop MCP server: {e}")
            return False
    
    async def handle_request(self, request: MCPRequest) -> MCPResponse:
        """
        Handle an MCP request.
        
        Args:
            request: The incoming request
            
        Returns:
            MCPResponse: The response to the request
        """
        try:
            self.logger.debug(f"Handling request: {request.method.value} (ID: {request.id})")
            
            # Check if server is running
            if not self.running:
                return self._create_error_response(
                    request,
                    -32001,
                    "Server not running",
                    {"request_id": request.id}
                )
            
            # Apply rate limiting (skip for ping requests)
            if request.method != RequestMethod.PING:
                client_id = request.id  # Using request ID as client identifier for now
                if not self.rate_limiter.is_allowed(client_id):
                    return self._create_error_response(
                        request,
                        -32002,
                        "Rate limit exceeded",
                        {"request_id": request.id, "retry_after": 60}
                    )
            
            # Find handler for the method
            handler = self.request_handlers.get(request.method)
            if not handler:
                return self._create_error_response(
                    request,
                    -32601,
                    f"Method not found: {request.method.value}",
                    {"request_id": request.id}
                )
            
            # Call the handler
            try:
                result = await handler(request)
                return self._create_success_response(request, result)
            except Exception as e:
                self.logger.error(f"Error in handler for {request.method.value}: {e}")
                return self._create_error_response(
                    request,
                    -32603,
                    f"Internal error: {str(e)}",
                    {"request_id": request.id, "exception": str(e)}
                )
                
        except Exception as e:
            self.logger.error(f"Failed to handle request: {e}")
            return self._create_error_response(
                request,
                -32603,
                f"Request handling failed: {str(e)}",
                {"request_id": request.id}
            )
    
    def authenticate_client(self, client_id: str, token: str) -> bool:
        """
        Authenticate a client and store their identity.
        
        Args:
            client_id: Client identifier
            token: Client's API token
            
        Returns:
            bool: True if authentication was successful, False otherwise
        """
        try:
            client_identity = self.auth_manager.authenticate_client(client_id, token)
            if client_identity:
                self.authenticated_clients[client_id] = client_identity
                self.logger.info(f"Client authenticated: {client_id}")
                return True
            else:
                self.logger.warning(f"Client authentication failed: {client_id}")
                return False
        except Exception as e:
            self.logger.error(f"Error authenticating client {client_id}: {e}")
            return False
    
    def is_client_authenticated(self, client_id: str) -> bool:
        """
        Check if a client is authenticated.
        
        Args:
            client_id: Client identifier
            
        Returns:
            bool: True if client is authenticated, False otherwise
        """
        return client_id in self.authenticated_clients
    
    def has_client_permission(self, client_id: str, permission: str) -> bool:
        """
        Check if an authenticated client has a specific permission.
        
        Args:
            client_id: Client identifier
            permission: Permission to check
            
        Returns:
            bool: True if client has the permission, False otherwise
        """
        if client_id not in self.authenticated_clients:
            return False
        
        client_identity = self.authenticated_clients[client_id]
        return self.auth_manager.has_permission(client_identity, permission)
    
    def add_valid_token(self, token: str, permissions: set = None) -> bool:
        """
        Add a valid token with associated permissions.
        
        Args:
            token: API token
            permissions: Set of permissions granted to this token
            
        Returns:
            bool: True if token was added successfully
        """
        return self.auth_manager.add_valid_token(token, permissions)
    
    def _create_success_response(self, request: MCPRequest, result: Dict[str, Any]) -> MCPResponse:
        """Create a successful response."""
        return MCPResponse(
            request_id=request.id,
            result=result,
            timestamp=datetime.now()
        )
    
    def _create_error_response(self, request: MCPRequest, code: int, message: str, 
                              data: Optional[Dict[str, Any]] = None) -> MCPResponse:
        """Create an error response."""
        error = {
            "code": code,
            "message": message
        }
        if data:
            error["data"] = data
            
        return MCPResponse(
            request_id=request.id,
            error=error,
            timestamp=datetime.now()
        )
    
    async def _handle_initialize(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle initialize request."""
        params = request.params or {}
        
        # Parse initialize parameters
        init_params = InitializeParams(
            protocol_version=params.get("protocolVersion", "2024-01-01"),
            capabilities=ClientCapabilities(**params.get("capabilities", {})),
            client_info=params.get("clientInfo"),
            locale=params.get("locale", "en-US")
        )
        
        # Create initialize result
        result = InitializeResult(
            protocol_version="2024-01-01",
            capabilities=self.capabilities,
            server_info={
                "name": "4S1T Agent AI MCP Server",
                "version": "0.1.0"
            }
        )
        
        # Store client info
        self.clients[request.id] = {
            "info": init_params.client_info,
            "capabilities": init_params.capabilities,
            "locale": init_params.locale,
            "connected_at": datetime.now()
        }
        
        self.logger.info(f"Client initialized: {init_params.client_info}")
        
        return {
            "protocolVersion": result.protocol_version,
            "capabilities": {
                "prompts": result.capabilities.prompts,
                "resources": result.capabilities.resources,
                "tools": result.capabilities.tools,
                "notifications": result.capabilities.notifications
            },
            "serverInfo": result.server_info
        }
    
    async def _handle_prompt_list(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle prompt list request."""
        prompts_list = [
            {
                "name": prompt.name,
                "description": prompt.description,
                "arguments": prompt.arguments
            }
            for prompt in self.prompts.values()
        ]
        
        return {"prompts": prompts_list}
    
    async def _handle_prompt_get(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle prompt get request."""
        params = request.params or {}
        prompt_name = params.get("name")
        
        if not prompt_name:
            raise ValueError("Prompt name is required")
        
        if prompt_name not in self.prompts:
            raise ValueError(f"Prompt not found: {prompt_name}")
        
        prompt = self.prompts[prompt_name]
        return {
            "name": prompt.name,
            "description": prompt.description,
            "arguments": prompt.arguments
        }
    
    async def _handle_resource_list(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle resource list request."""
        start_time = datetime.now()
        
        try:
            resources_list = [
                {
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description,
                    "mimeType": resource.mime_type,
                    "size": resource.size,
                    "createdAt": resource.created_at.isoformat() if resource.created_at else None,
                    "modifiedAt": resource.modified_at.isoformat() if resource.modified_at else None,
                    "etag": resource.etag
                }
                for resource in self.resources.values()
            ]
            
            # Log successful resource list access
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            log_entry = ResourceAccessLogEntry(
                timestamp=datetime.now().isoformat(),
                client_id=request.id,
                resource_uri="ALL_RESOURCES",
                operation="LIST",
                success=True,
                execution_time_ms=execution_time
            )
            self.audit_logger.log_resource_access(log_entry)
            
            return {"resources": resources_list}
            
        except Exception as e:
            # Log failed resource list access
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            log_entry = ResourceAccessLogEntry(
                timestamp=datetime.now().isoformat(),
                client_id=request.id,
                resource_uri="ALL_RESOURCES",
                operation="LIST",
                success=False,
                error_message=str(e),
                execution_time_ms=execution_time
            )
            self.audit_logger.log_resource_access(log_entry)
            
            raise
    
    async def _handle_resource_get(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle resource get request."""
        start_time = datetime.now()
        params = request.params or {}
        uri = params.get("uri")
        
        if not uri:
            # Log failed resource access - missing URI
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            log_entry = ResourceAccessLogEntry(
                timestamp=datetime.now().isoformat(),
                client_id=request.id,
                resource_uri="MISSING_URI",
                operation="GET",
                success=False,
                error_message="URI is required",
                execution_time_ms=execution_time
            )
            self.audit_logger.log_resource_access(log_entry)
            
            raise ValueError("URI is required")
        
        try:
            # In a real implementation, this would fetch the actual resource content
            # For now, we'll simulate it
            if uri not in self.resources:
                # Log failed resource access - resource not found
                execution_time = (datetime.now() - start_time).total_seconds() * 1000
                log_entry = ResourceAccessLogEntry(
                    timestamp=datetime.now().isoformat(),
                    client_id=request.id,
                    resource_uri=uri,
                    operation="GET",
                    success=False,
                    error_message=f"Resource not found: {uri}",
                    execution_time_ms=execution_time
                )
                self.audit_logger.log_resource_access(log_entry)
                
                raise ValueError(f"Resource not found: {uri}")
            
            resource = self.resources[uri]
            
            # Check cache first
            cached_content = self._get_cached_resource(uri)
            if cached_content:
                contents = cached_content
                self.logger.debug(f"Retrieved resource {uri} from cache")
            else:
                # Simulate resource contents
                contents = f"Contents of resource: {resource.name}\nDescription: {resource.description}"
                # Cache the content
                self._cache_resource(uri, contents)
                self.logger.debug(f"Cached resource {uri}")
            
            # Log successful resource access
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            log_entry = ResourceAccessLogEntry(
                timestamp=datetime.now().isoformat(),
                client_id=request.id,
                resource_uri=uri,
                operation="GET",
                success=True,
                execution_time_ms=execution_time
            )
            self.audit_logger.log_resource_access(log_entry)
            
            return {
                "uri": uri,
                "contents": contents,
                "mimeType": resource.mime_type,
                "size": len(contents),
                "etag": resource.etag
            }
            
        except Exception as e:
            # If we haven't already logged the error, log it now
            if "Resource not found" not in str(e) and "URI is required" not in str(e):
                execution_time = (datetime.now() - start_time).total_seconds() * 1000
                log_entry = ResourceAccessLogEntry(
                    timestamp=datetime.now().isoformat(),
                    client_id=request.id,
                    resource_uri=uri or "UNKNOWN",
                    operation="GET",
                    success=False,
                    error_message=str(e),
                    execution_time_ms=execution_time
                )
                self.audit_logger.log_resource_access(log_entry)
            
            raise
    
    async def _handle_tool_list(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle tool list request."""
        tools_list = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "outputSchema": tool.output_schema
            }
            for tool in self.tools.values()
        ]
        
        return {"tools": tools_list}
    
    async def _handle_tool_call(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle tool call request."""
        start_time = datetime.now()
        params = request.params or {}
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if not tool_name:
            # Log failed tool execution - missing tool name
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            log_entry = ToolExecutionLogEntry(
                timestamp=datetime.now().isoformat(),
                client_id=request.id,
                tool_name="MISSING_TOOL_NAME",
                success=False,
                execution_time_ms=execution_time,
                error_message="Tool name is required"
            )
            self.audit_logger.log_tool_execution(log_entry)
            
            raise ValueError("Tool name is required")
        
        # Use the new tool framework for execution
        try:
            tool_result = await self.tool_executor.execute_tool(tool_name, arguments)
            
            # Log tool execution
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            log_entry = ToolExecutionLogEntry(
                timestamp=datetime.now().isoformat(),
                client_id=request.id,
                tool_name=tool_name,
                success=not tool_result.is_error,
                execution_time_ms=execution_time,
                error_message=tool_result.result if tool_result.is_error else None
            )
            self.audit_logger.log_tool_execution(log_entry)
            
            return {
                "toolName": tool_result.tool_name,
                "result": tool_result.result,
                "isError": tool_result.is_error
            }
        except Exception as e:
            # Log failed tool execution
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            log_entry = ToolExecutionLogEntry(
                timestamp=datetime.now().isoformat(),
                client_id=request.id,
                tool_name=tool_name,
                success=False,
                execution_time_ms=execution_time,
                error_message=str(e)
            )
            self.audit_logger.log_tool_execution(log_entry)
            
            self.logger.error(f"Tool execution failed for {tool_name}: {e}")
            return {
                "toolName": tool_name,
                "result": str(e),
                "isError": True
            }
    
    async def _handle_ping(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle ping request."""
        return {"pong": True, "timestamp": datetime.now().isoformat()}
    
    async def _handle_resource_subscribe(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle resource subscribe request."""
        params = request.params or {}
        uri = params.get("uri")
        
        if not uri:
            raise ValueError("URI is required for subscription")
        
        if uri not in self.resources:
            raise ValueError(f"Resource not found: {uri}")
        
        # In a full implementation, we would set up actual subscriptions
        # For now, we'll just acknowledge the subscription
        self.logger.info(f"Client {request.id} subscribed to resource: {uri}")
        
        return {
            "uri": uri,
            "subscribed": True
        }
    
    async def _handle_resource_unsubscribe(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle resource unsubscribe request."""
        params = request.params or {}
        uri = params.get("uri")
        
        if not uri:
            raise ValueError("URI is required for unsubscription")
        
        # In a full implementation, we would cancel the subscription
        # For now, we'll just acknowledge the unsubscription
        self.logger.info(f"Client {request.id} unsubscribed from resource: {uri}")
        
        return {
            "uri": uri,
            "unsubscribed": True
        }
    
    async def _handle_notifications_capabilities(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle notifications/capabilities request."""
        # This would typically return information about what notifications the server can send
        return {
            "notifications": [
                {
                    "method": "notifications/resources",
                    "description": "Resource change notifications"
                },
                {
                    "method": "notifications/tools",
                    "description": "Tool execution notifications"
                },
                {
                    "method": "notifications/prompts",
                    "description": "Prompt execution notifications"
                }
            ]
        }
    
    async def _handle_notifications_resources(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle notifications/resources request."""
        # This would configure resource change notifications for the client
        params = request.params or {}
        uris = params.get("uris", [])
        
        # Log the notification request
        log_entry = NotificationLogEntry(
            timestamp=datetime.now().isoformat(),
            client_id=request.id,
            notification_type="resources",
            event_type="requested",
            success=True,
            payload_size_bytes=len(str(params)) if params else 0
        )
        self.audit_logger.log_notification(log_entry)
        
        # In a full implementation, we would set up actual notification subscriptions
        # For now, we'll just acknowledge the request
        self.logger.info(f"Client {request.id} requested resource notifications for URIs: {uris}")
        
        return {
            "subscribed": True,
            "uris": uris
        }
    
    async def _handle_notifications_tools(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle notifications/tools request."""
        # This would configure tool execution notifications for the client
        params = request.params or {}
        tools = params.get("tools", [])
        
        # Log the notification request
        log_entry = NotificationLogEntry(
            timestamp=datetime.now().isoformat(),
            client_id=request.id,
            notification_type="tools",
            event_type="requested",
            success=True,
            payload_size_bytes=len(str(params)) if params else 0
        )
        self.audit_logger.log_notification(log_entry)
        
        # In a full implementation, we would set up actual notification subscriptions
        # For now, we'll just acknowledge the request
        self.logger.info(f"Client {request.id} requested tool notifications for tools: {tools}")
        
        return {
            "subscribed": True,
            "tools": tools
        }
    
    async def _handle_notifications_prompts(self, request: MCPRequest) -> Dict[str, Any]:
        """Handle notifications/prompts request."""
        # This would configure prompt execution notifications for the client
        params = request.params or {}
        prompts = params.get("prompts", [])
        
        # Log the notification request
        log_entry = NotificationLogEntry(
            timestamp=datetime.now().isoformat(),
            client_id=request.id,
            notification_type="prompts",
            event_type="requested",
            success=True,
            payload_size_bytes=len(str(params)) if params else 0
        )
        self.audit_logger.log_notification(log_entry)
        
        # In a full implementation, we would set up actual notification subscriptions
        # For now, we'll just acknowledge the request
        self.logger.info(f"Client {request.id} requested prompt notifications for prompts: {prompts}")
        
        return {
            "subscribed": True,
            "prompts": prompts
        }
    
    def register_resource(self, resource: Resource) -> bool:
        """
        Register a resource with the server.
        
        Args:
            resource: The resource to register
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            self.resources[resource.uri] = resource
            self.logger.debug(f"Registered resource: {resource.uri}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register resource {resource.uri}: {e}")
            return False
    
    def unregister_resource(self, uri: str) -> bool:
        """
        Unregister a resource from the server.
        
        Args:
            uri: URI of the resource to unregister
            
        Returns:
            bool: True if unregistration was successful, False otherwise
        """
        try:
            if uri in self.resources:
                del self.resources[uri]
                self.logger.debug(f"Unregistered resource: {uri}")
                return True
            return False
        except Exception as e:
            self.logger.error(f"Failed to unregister resource {uri}: {e}")
            return False
    
    def register_tool(self, tool: Tool, executor: Callable) -> bool:
        """
        Register a tool with the server.
        
        Args:
            tool: The tool to register
            executor: Function to execute the tool
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            self.tools[tool.name] = tool
            self.tool_executors[tool.name] = executor
            self.logger.debug(f"Registered tool: {tool.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register tool {tool.name}: {e}")
            return False
    
    def unregister_tool(self, name: str) -> bool:
        """
        Unregister a tool from the server.
        
        Args:
            name: Name of the tool to unregister
            
        Returns:
            bool: True if unregistration was successful, False otherwise
        """
        try:
            if name in self.tools:
                del self.tools[name]
            if name in self.tool_executors:
                del self.tool_executors[name]
            self.logger.debug(f"Unregistered tool: {name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to unregister tool {name}: {e}")
            return False
    
    def register_prompt(self, prompt: Prompt) -> bool:
        """
        Register a prompt with the server.
        
        Args:
            prompt: The prompt to register
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            self.prompts[prompt.name] = prompt
            self.logger.debug(f"Registered prompt: {prompt.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register prompt {prompt.name}: {e}")
            return False
    
    def unregister_prompt(self, name: str) -> bool:
        """
        Unregister a prompt from the server.
        
        Args:
            name: Name of the prompt to unregister
            
        Returns:
            bool: True if unregistration was successful, False otherwise
        """
        try:
            if name in self.prompts:
                del self.prompts[name]
                self.logger.debug(f"Unregistered prompt: {name}")
                return True
            return False
        except Exception as e:
            self.logger.error(f"Failed to unregister prompt {name}: {e}")
            return False
    
    def get_server_info(self) -> Dict[str, Any]:
        """
        Get server information and statistics.
        
        Returns:
            Dict[str, Any]: Server information
        """
        return {
            "running": self.running,
            "resources_count": len(self.resources),
            "tools_count": len(self.tools),
            "prompts_count": len(self.prompts),
            "clients_count": len(self.clients),
            "configuration": {
                "max_resource_size": self.config.max_resource_size,
                "timeout_seconds": self.config.timeout_seconds,
                "enable_logging": self.config.enable_logging
            }
        }
    
    def _get_cached_resource(self, uri: str) -> Optional[str]:
        """
        Get cached resource content if available and not expired.
        
        Args:
            uri: Resource URI
            
        Returns:
            Cached content if available and not expired, None otherwise
        """
        if uri not in self.resource_cache:
            return None
        
        cache_entry = self.resource_cache[uri]
        timestamp = cache_entry.get("timestamp")
        ttl = cache_entry.get("ttl", self.cache_ttl_seconds)
        
        # Check if cache entry is expired
        if timestamp and (datetime.now() - timestamp).total_seconds() > ttl:
            # Remove expired entry
            del self.resource_cache[uri]
            return None
        
        return cache_entry.get("content")
    
    def _cache_resource(self, uri: str, content: str, ttl: Optional[int] = None):
        """
        Cache resource content.
        
        Args:
            uri: Resource URI
            content: Resource content to cache
            ttl: Time to live in seconds (defaults to cache_ttl_seconds)
        """
        self.resource_cache[uri] = {
            "content": content,
            "timestamp": datetime.now(),
            "ttl": ttl or self.cache_ttl_seconds
        }
    
    def clear_resource_cache(self, uri: Optional[str] = None):
        """
        Clear resource cache.
        
        Args:
            uri: Specific URI to clear, or None to clear all cache
        """
        if uri:
            if uri in self.resource_cache:
                del self.resource_cache[uri]
                self.logger.debug(f"Cleared cache for resource: {uri}")
        else:
            self.resource_cache.clear()
            self.logger.debug("Cleared all resource cache")


# Example tool executor functions
async def example_calculator_executor(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Example calculator tool executor."""
    operation = arguments.get("operation")
    a = arguments.get("a", 0)
    b = arguments.get("b", 0)
    
    if operation == "add":
        result = a + b
    elif operation == "subtract":
        result = a - b
    elif operation == "multiply":
        result = a * b
    elif operation == "divide":
        result = a / b if b != 0 else "Cannot divide by zero"
    else:
        raise ValueError(f"Unknown operation: {operation}")
    
    return {"result": result, "operation": operation}


async def example_web_search_executor(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Example web search tool executor."""
    query = arguments.get("query", "")
    max_results = arguments.get("maxResults", 5)
    
    # Simulate search results
    results = [
        {"title": f"Result {i} for '{query}'", "url": f"https://example.com/{i}", "snippet": f"Snippet for result {i}"}
        for i in range(1, max_results + 1)
    ]
    
    return {"results": results, "query": query, "count": len(results)}


# Example usage
if __name__ == "__main__":
    # Create server
    server = MCPServer()
    
    # Register example resources
    example_resource = Resource(
        uri="file:///example.txt",
        name="Example Resource",
        description="An example resource for testing",
        mime_type="text/plain"
    )
    server.register_resource(example_resource)
    
    # Register example tools
    calculator_tool = Tool(
        name="calculator",
        description="Performs basic arithmetic operations",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
                "a": {"type": "number"},
                "b": {"type": "number"}
            },
            "required": ["operation", "a", "b"]
        }
    )
    server.register_tool(calculator_tool, example_calculator_executor)
    
    web_search_tool = Tool(
        name="web_search",
        description="Searches the web for information",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "maxResults": {"type": "integer", "default": 5}
            },
            "required": ["query"]
        }
    )
    server.register_tool(web_search_tool, example_web_search_executor)
    
    # Register example prompt
    example_prompt = Prompt(
        name="summarize",
        description="Summarizes text content",
        arguments=[
            {"name": "text", "description": "Text to summarize", "required": True},
            {"name": "length", "description": "Summary length in sentences", "required": False, "default": 3}
        ]
    )
    server.register_prompt(example_prompt)
    
    # Start server
    asyncio.run(server.start())
    
    # Print server info
    info = server.get_server_info()
    print(f"Server info: {info}")


# Global MCP server instance reference
global_mcp_server: Optional[MCPServer] = None


# Export the example tools for import
__all__ = [
    "MCPServer",
    "global_mcp_server",
    "calculator_tool",
    "web_search_tool",
    "example_calculator_executor",
    "example_web_search_executor"
]


# Define the example tools at module level for import
calculator_tool = Tool(
    name="calculator",
    description="Performs basic arithmetic operations",
    input_schema={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
            "a": {"type": "number"},
            "b": {"type": "number"}
        },
        "required": ["operation", "a", "b"]
    }
)

web_search_tool = Tool(
    name="web_search",
    description="Searches the web for information",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "maxResults": {"type": "integer", "default": 5}
        },
        "required": ["query"]
    }
)
