"""
Debug HTTP adapter issues.
"""

import sys
import os

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient
from mcp.mcp_types import Resource
from mcp.server import MCPServer
from mcp.http_adapter import MCPHTTPAdapter


def debug_resource_endpoints():
    """Debug resource endpoints issue."""
    # Create MCP server and adapter
    mcp_server = MCPServer()
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Create test client
    client = TestClient(adapter.app)
    
    # Register a resource
    resource = Resource(
        uri="file:///test.txt",
        name="Test Resource",
        description="A test resource"
    )
    mcp_server.register_resource(resource)
    
    # Test resources list endpoint
    print("Testing /mcp/resources endpoint...")
    response = client.get("/mcp/resources")
    print(f"Status code: {response.status_code}")
    print(f"Response: {response.text}")
    
    if response.status_code != 200:
        print("Error occurred!")
        return
    
    data = response.json()
    print(f"Data: {data}")


if __name__ == "__main__":
    debug_resource_endpoints()
