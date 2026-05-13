"""
HTTP adapter for the MCP (Model Context Protocol) server.

This module provides an HTTP interface for the MCP server, allowing clients
to communicate with the MCP server over HTTP/JSON.
"""

import json
from typing import Dict, Any, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .mcp_types import MCPRequest, MCPResponse, RequestMethod
from .server import MCPServer

from utils.logger import setup_logger
logger = setup_logger(__name__)


class MCPHTTPAdapter:
    """
    HTTP adapter for MCP server.
    
    This adapter exposes the MCP server functionality over HTTP/JSON,
    making it accessible to web-based clients and other HTTP-capable systems.
    """
    
    def __init__(self, mcp_server: MCPServer, app: Optional[FastAPI] = None):
        """
        Initialize the HTTP adapter.
        
        Args:
            mcp_server: The MCP server to adapt
            app: FastAPI app instance (creates new one if None)
        """
        self.mcp_server = mcp_server
        self.app = app or FastAPI(title="MCP Server", version="0.1.0")
        self.setup_routes()
        self.setup_middleware()
        self.logger = logger
    
    def setup_middleware(self):
        """Setup CORS middleware and other middleware."""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # In production, restrict this
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    def setup_routes(self):
        """Setup HTTP routes for MCP methods."""
        # Health check endpoint
        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "timestamp": datetime.now().isoformat()}
        
        # MCP protocol endpoint
        @self.app.post("/mcp")
        async def handle_mcp_request(request: Request):
            try:
                # Parse request body
                body = await request.json()
                self.logger.debug(f"Received MCP request: {body}")
                
                # Extract authentication headers if present
                client_id = request.headers.get("X-Client-ID")
                token = request.headers.get("Authorization", "").replace("Bearer ", "")
                
                # If authentication headers are present, authenticate the client
                if client_id and token:
                    auth_result = self.mcp_server.authenticate_client(client_id, token)
                    if not auth_result:
                        raise HTTPException(status_code=401, detail="Authentication failed")
                
                # Convert to MCPRequest
                method_str = body.get("method", "")
                # Convert string method to RequestMethod enum
                try:
                    method = RequestMethod(method_str)
                except ValueError:
                    method = RequestMethod.PING  # Default fallback
                
                # Use client_id as request ID if available, otherwise generate one
                request_id = client_id if client_id else body.get("id", "")
                
                mcp_request = MCPRequest(
                    id=request_id,
                    method=method,
                    params=body.get("params", {}),
                    timestamp=datetime.now()
                )
                
                # Handle with MCP server
                mcp_response = await self.mcp_server.handle_request(mcp_request)
                
                # Convert to dict for JSON response
                response_dict = {
                    "id": mcp_response.id,
                    "requestId": mcp_response.request_id,
                }
                
                if mcp_response.result is not None:
                    response_dict["result"] = mcp_response.result
                
                if mcp_response.error is not None:
                    response_dict["error"] = mcp_response.error
                
                response_dict["timestamp"] = mcp_response.timestamp.isoformat()
                
                self.logger.debug(f"Sending MCP response: {response_dict}")
                return response_dict
                
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON in request: {e}")
                raise HTTPException(status_code=400, detail="Invalid JSON")
            except HTTPException:
                # Re-raise HTTP exceptions
                raise
            except Exception as e:
                self.logger.error(f"Error handling MCP request: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        # Server info endpoint
        @self.app.get("/mcp/info")
        async def get_server_info():
            info = self.mcp_server.get_server_info()
            return info
        
        # Resources endpoints
        @self.app.get("/mcp/resources")
        async def list_resources():
            request = MCPRequest(method=RequestMethod.RESOURCE_LIST)
            response = await self.mcp_server.handle_request(request)
            if response.error:
                raise HTTPException(status_code=500, detail=response.error)
            return response.result
        
        @self.app.get("/mcp/resources/{uri:path}")
        async def get_resource(uri: str):
            request = MCPRequest(
                method=RequestMethod.RESOURCE_GET,
                params={"uri": uri}
            )
            response = await self.mcp_server.handle_request(request)
            if response.error:
                raise HTTPException(status_code=404, detail=response.error.get("message", "Resource not found"))
            return response.result
        
        # Tools endpoints
        @self.app.get("/mcp/tools")
        async def list_tools():
            request = MCPRequest(method=RequestMethod.TOOL_LIST)
            response = await self.mcp_server.handle_request(request)
            if response.error:
                raise HTTPException(status_code=500, detail=response.error)
            return response.result
        
        @self.app.post("/mcp/tools/{tool_name}")
        async def call_tool(tool_name: str, arguments: Dict[str, Any]):
            request = MCPRequest(
                method=RequestMethod.TOOL_CALL,
                params={"name": tool_name, "arguments": arguments}
            )
            response = await self.mcp_server.handle_request(request)
            if response.error:
                raise HTTPException(status_code=500, detail=response.error)
            return response.result
        
        # Prompts endpoints
        @self.app.get("/mcp/prompts")
        async def list_prompts():
            request = MCPRequest(method=RequestMethod.PROMPT_LIST)
            response = await self.mcp_server.handle_request(request)
            if response.error:
                raise HTTPException(status_code=500, detail=response.error)
            return response.result
        
        @self.app.get("/mcp/prompts/{prompt_name}")
        async def get_prompt(prompt_name: str):
            request = MCPRequest(
                method=RequestMethod.PROMPT_GET,
                params={"name": prompt_name}
            )
            response = await self.mcp_server.handle_request(request)
            if response.error:
                raise HTTPException(status_code=404, detail=response.error.get("message", "Prompt not found"))
            return response.result
    
    def get_app(self) -> FastAPI:
        """
        Get the FastAPI application instance.
        
        Returns:
            FastAPI: The FastAPI application
        """
        return self.app
    
    async def start_server(self, host: str = "127.0.0.1", port: int = 8000):
        """
        Start the HTTP server.
        
        Args:
            host: Host to bind to
            port: Port to listen on
        """
        try:
            # Start MCP server
            await self.mcp_server.start()
            
            # Start HTTP server
            self.logger.info(f"Starting MCP HTTP server on {host}:{port}")
            uvicorn.run(self.app, host=host, port=port)
            
        except Exception as e:
            self.logger.error(f"Failed to start HTTP server: {e}")
            raise


# Example usage
if __name__ == "__main__":
    # Create MCP server
    mcp_server = MCPServer()
    
    # Create HTTP adapter
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Start server (this would block)
    # asyncio.run(adapter.start_server())
    
    # For demonstration, just print the app info
    print("MCP HTTP Adapter created successfully")
    print(f"Available endpoints: {[route.path for route in adapter.app.routes]}")
