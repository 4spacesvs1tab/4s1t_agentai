"""
Audit logging demo for the MCP (Model Context Protocol) implementation.

This demo shows how to use the audit logging features
of the MCP server.
"""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.mcp_types import MCPRequest, RequestMethod, Resource, Tool
from mcp.server import MCPServer


async def demo_audit_logging():
    """Demonstrate audit logging features."""
    print("=== MCP Audit Logging Demo ===\n")
    
    # Create MCP server
    server = MCPServer()
    await server.start()
    print("✓ MCP Server started")
    
    # Register a test resource
    test_resource = Resource(
        uri="file:///demo/test.txt",
        name="Demo Resource",
        description="A resource for testing audit logging",
        mime_type="text/plain"
    )
    server.register_resource(test_resource)
    print("✓ Registered test resource")
    
    # Register a test tool
    async def demo_calculator(arguments):
        """Demo calculator tool."""
        operation = arguments.get("operation", "add")
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        
        await asyncio.sleep(0.1)  # Simulate some work
        
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
        name="demo_calculator",
        description="Demo calculator for audit logging",
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
    
    server.register_tool(calculator_tool, demo_calculator)
    print("✓ Registered demo calculator tool")
    
    # Test resource list access logging
    print("\n1. Testing resource list access logging...")
    request = MCPRequest(
        method=RequestMethod.RESOURCE_LIST,
        id="demo_client_1"
    )
    response = await server.handle_request(request)
    if response.result and not response.error:
        print("   ✓ Resource list access logged successfully")
    else:
        print(f"   ✗ Resource list access failed: {response.error}")
    
    # Test resource get access logging
    print("\n2. Testing resource get access logging...")
    request = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={"uri": "file:///demo/test.txt"},
        id="demo_client_2"
    )
    response = await server.handle_request(request)
    if response.result and not response.error:
        print("   ✓ Resource get access logged successfully")
    else:
        print(f"   ✗ Resource get access failed: {response.error}")
    
    # Test tool execution logging
    print("\n3. Testing tool execution logging...")
    request = MCPRequest(
        method=RequestMethod.TOOL_CALL,
        params={
            "name": "demo_calculator",
            "arguments": {"operation": "add", "a": 10, "b": 5}
        },
        id="demo_client_3"
    )
    response = await server.handle_request(request)
    if response.result and not response.error:
        print("   ✓ Tool execution logged successfully")
        print(f"   Result: {response.result}")
    else:
        print(f"   ✗ Tool execution failed: {response.error}")
    
    # Test failed access logging
    print("\n4. Testing failed access logging...")
    
    # Test resource not found
    request = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={"uri": "file:///demo/nonexistent.txt"},
        id="demo_client_4"
    )
    response = await server.handle_request(request)
    if response.error:
        print("   ✓ Failed resource access logged successfully")
    else:
        print("   ✗ Expected error for nonexistent resource")
    
    # Test tool not found
    request = MCPRequest(
        method=RequestMethod.TOOL_CALL,
        params={
            "name": "nonexistent_tool",
            "arguments": {}
        },
        id="demo_client_5"
    )
    response = await server.handle_request(request)
    if response.error:
        print("   ✓ Failed tool execution logged successfully")
    else:
        print("   ✗ Expected error for nonexistent tool")
    
    # Show recent log entries
    print("\n5. Recent audit log entries:")
    try:
        log_entries = server.audit_logger.get_log_entries(10)
        for i, entry in enumerate(log_entries[-5:], 1):  # Show last 5 entries
            print(f"   {i}. {entry}")
    except Exception as e:
        print(f"   ✗ Failed to retrieve log entries: {e}")
    
    print("\n=== Audit Logging Demo Completed ===")


async def main():
    """Run the audit logging demo."""
    await demo_audit_logging()


if __name__ == "__main__":
    asyncio.run(main())
