"""
Integration test for the complete MCP implementation.

This test verifies that all MCP components work together correctly.
"""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.mcp_types import MCPRequest, RequestMethod, Resource, Tool
from mcp.server import MCPServer
from mcp.http_adapter import MCPHTTPAdapter
from fastapi.testclient import TestClient


async def test_complete_mcp_flow():
    """Test a complete flow through the MCP system."""
    print("=== MCP Integration Test ===\n")
    
    # Create MCP server
    server = MCPServer()
    await server.start()
    print("✓ MCP Server started")
    
    # Create HTTP adapter
    adapter = MCPHTTPAdapter(server)
    client = TestClient(adapter.app)
    print("✓ HTTP Adapter created")
    
    # Test 1: Health check via HTTP
    print("\n1. Testing HTTP health check...")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    print("   ✓ Health check passed")
    
    # Test 2: Initialize via HTTP
    print("\n2. Testing HTTP initialize...")
    request_data = {
        "id": "test-init-1",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-01-01",
            "capabilities": {
                "prompts": True,
                "resources": True,
                "tools": True
            },
            "clientInfo": {
                "name": "Integration Test Client",
                "version": "1.0.0"
            }
        }
    }
    response = client.post("/mcp", json=request_data)
    assert response.status_code == 200
    result = response.json()
    assert "result" in result
    assert result["result"]["protocolVersion"] == "2024-01-01"
    print("   ✓ HTTP initialize passed")
    
    # Test 3: Register resources
    print("\n3. Registering test resources...")
    resource = Resource(
        uri="file:///test/integration.txt",
        name="Integration Test Resource",
        description="Resource for integration testing",
        mime_type="text/plain"
    )
    assert server.register_resource(resource) == True
    print("   ✓ Resource registered")
    
    # Test 4: List resources via HTTP
    print("\n4. Testing HTTP resource listing...")
    response = client.get("/mcp/resources")
    assert response.status_code == 200
    data = response.json()
    assert "resources" in data
    assert len(data["resources"]) >= 1
    print("   ✓ HTTP resource listing passed")
    
    # Test 5: Register tool
    print("\n5. Registering test tool...")
    async def echo_executor(arguments):
        return {"echo": arguments.get("message", "No message")}
    
    tool = Tool(
        name="echo",
        description="Echoes back the provided message",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string"}
            },
            "required": ["message"]
        }
    )
    assert server.register_tool(tool, echo_executor) == True
    print("   ✓ Tool registered")
    
    # Test 6: List tools via HTTP
    print("\n6. Testing HTTP tool listing...")
    response = client.get("/mcp/tools")
    assert response.status_code == 200
    data = response.json()
    assert "tools" in data
    assert len(data["tools"]) >= 1
    print("   ✓ HTTP tool listing passed")
    
    # Test 7: Call tool via HTTP
    print("\n7. Testing HTTP tool call...")
    response = client.post("/mcp/tools/echo", json={"message": "Hello MCP!"})
    assert response.status_code == 200
    data = response.json()
    assert "result" in data
    assert data["result"]["echo"] == "Hello MCP!"
    print("   ✓ HTTP tool call passed")
    
    # Test 8: Server info via HTTP
    print("\n8. Testing HTTP server info...")
    response = client.get("/mcp/info")
    assert response.status_code == 200
    data = response.json()
    assert "running" in data
    assert data["running"] == True
    assert data["tools_count"] >= 1
    assert data["resources_count"] >= 1
    print("   ✓ HTTP server info passed")
    
    # Test 9: Direct server interaction
    print("\n9. Testing direct server interaction...")
    request = MCPRequest(method=RequestMethod.PING)
    response = await server.handle_request(request)
    assert response.result is not None
    assert response.result["pong"] == True
    print("   ✓ Direct server interaction passed")
    
    print("\n=== All Integration Tests Passed! ===")
    return True


async def main():
    """Run the integration test."""
    try:
        await test_complete_mcp_flow()
        print("\n🎉 MCP Integration Test Successful!")
        return True
    except Exception as e:
        print(f"\n❌ MCP Integration Test Failed: {e}")
        return False


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
