"""
Demo script for the MCP (Model Context Protocol) implementation.

This script demonstrates the basic functionality of the MCP server and HTTP adapter.
"""

import asyncio
import json
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient

from mcp.mcp_types import MCPRequest, RequestMethod, Resource, Tool, Prompt
from mcp.server import MCPServer
from mcp.http_adapter import MCPHTTPAdapter


async def demo_mcp_server():
    """Demonstrate MCP server functionality."""
    print("=== MCP Server Demo ===\n")
    
    # Create MCP server
    server = MCPServer()
    await server.start()
    print("✓ MCP Server started")
    
    # Test ping
    print("\n1. Testing ping request...")
    request = MCPRequest(method=RequestMethod.PING)
    response = await server.handle_request(request)
    print(f"   Ping response: {response.result}")
    
    # Test initialize
    print("\n2. Testing initialize request...")
    request = MCPRequest(
        method=RequestMethod.INITIALIZE,
        params={
            "protocolVersion": "2024-01-01",
            "capabilities": {
                "prompts": True,
                "resources": True,
                "tools": True
            },
            "clientInfo": {
                "name": "Demo Client",
                "version": "1.0.0"
            }
        }
    )
    response = await server.handle_request(request)
    print(f"   Initialize response: {response.result}")
    
    # Register resources
    print("\n3. Registering resources...")
    resource1 = Resource(
        uri="file:///documents/readme.md",
        name="README Document",
        description="Project README file",
        mime_type="text/markdown"
    )
    resource2 = Resource(
        uri="file:///data/users.json",
        name="User Data",
        description="User information database",
        mime_type="application/json"
    )
    
    server.register_resource(resource1)
    server.register_resource(resource2)
    print("   ✓ Registered 2 resources")
    
    # List resources
    print("\n4. Listing resources...")
    request = MCPRequest(method=RequestMethod.RESOURCE_LIST)
    response = await server.handle_request(request)
    print(f"   Found {len(response.result['resources'])} resources:")
    for resource in response.result["resources"]:
        print(f"   - {resource['name']} ({resource['uri']})")
    
    # Register tools
    print("\n5. Registering tools...")
    async def calculator_executor(arguments):
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
    
    server.register_tool(calculator_tool, calculator_executor)
    print("   ✓ Registered calculator tool")
    
    # List tools
    print("\n6. Listing tools...")
    request = MCPRequest(method=RequestMethod.TOOL_LIST)
    response = await server.handle_request(request)
    print(f"   Found {len(response.result['tools'])} tools:")
    for tool in response.result["tools"]:
        print(f"   - {tool['name']}: {tool['description']}")
    
    # Test tool call
    print("\n7. Testing tool call...")
    request = MCPRequest(
        method=RequestMethod.TOOL_CALL,
        params={
            "name": "calculator",
            "arguments": {"operation": "add", "a": 10, "b": 5}
        }
    )
    response = await server.handle_request(request)
    print(f"   Calculator result: {response.result}")
    
    # Get server info
    print("\n8. Getting server info...")
    info = server.get_server_info()
    print(f"   Server info: {info}")
    
    print("\n=== MCP Server Demo Complete ===")


def demo_http_adapter():
    """Demonstrate HTTP adapter functionality."""
    print("\n=== MCP HTTP Adapter Demo ===\n")
    
    # Create MCP server and HTTP adapter
    server = MCPServer()
    adapter = MCPHTTPAdapter(server)
    client = TestClient(adapter.app)
    
    # Start server (in a real scenario, this would be done asynchronously)
    # For demo purposes, we'll just test the endpoints
    
    # Test health endpoint
    print("1. Testing health endpoint...")
    response = client.get("/health")
    print(f"   Health response: {response.json()}")
    
    # Test MCP info endpoint
    print("\n2. Testing MCP info endpoint...")
    response = client.get("/mcp/info")
    print(f"   Info response: {response.json()}")
    
    print("\n=== HTTP Adapter Demo Complete ===")


async def main():
    """Run all demos."""
    await demo_mcp_server()
    demo_http_adapter()


if __name__ == "__main__":
    asyncio.run(main())
