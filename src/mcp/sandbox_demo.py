"""
Sandbox demo for the MCP (Model Context Protocol) implementation.

This demo shows how to use the tool execution sandbox features
of the MCP server.
"""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.mcp_types import MCPRequest, RequestMethod, Resource, Tool
from mcp.server import MCPServer
from mcp.sandbox import SandboxConfig


async def demo_sandbox_features():
    """Demonstrate sandbox features."""
    print("=== MCP Sandbox Demo ===\n")
    
    # Create MCP server with custom sandbox configuration
    server = MCPServer()
    await server.start()
    print("✓ MCP Server started")
    
    # Show sandbox status
    sandbox_status = server.tool_sandbox.get_sandbox_status()
    print(f"✓ Sandbox configuration:")
    print(f"  - Timeout: {sandbox_status['timeout_seconds']} seconds")
    print(f"  - Max concurrent executions: {sandbox_status['max_concurrent']}")
    print(f"  - Memory limit: {sandbox_status['memory_limit_mb']} MB")
    print(f"  - Network access: {'Allowed' if sandbox_status['allow_network'] else 'Blocked'}")
    
    # Register a test tool
    async def test_calculator(arguments):
        """Test calculator tool that simulates computation."""
        operation = arguments.get("operation", "add")
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        
        # Simulate some processing time
        await asyncio.sleep(0.1)
        
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
    
    server.register_tool(calculator_tool, test_calculator)
    print("\n✓ Registered calculator tool with sandbox support")
    
    # Test normal tool execution
    print("\n1. Testing normal tool execution...")
    request = MCPRequest(
        method=RequestMethod.TOOL_CALL,
        params={
            "name": "calculator",
            "arguments": {"operation": "add", "a": 10, "b": 5}
        }
    )
    
    response = await server.handle_request(request)
    if response.result and not response.error:
        print(f"   ✓ Normal execution successful")
        print(f"   Result: {response.result}")
    else:
        print(f"   ✗ Normal execution failed: {response.error}")
    
    # Test tool execution with timeout
    print("\n2. Testing tool execution with built-in timeout...")
    
    # Create a tool that will timeout
    async def slow_tool(arguments):
        """Tool that takes longer than the timeout."""
        # This will exceed the default 30-second timeout
        await asyncio.sleep(35)
        return {"result": "This should not be reached"}
    
    slow_tool_def = Tool(
        name="slow_tool",
        description="A tool that takes too long to execute",
        input_schema={"type": "object"}
    )
    
    server.register_tool(slow_tool_def, slow_tool)
    
    request = MCPRequest(
        method=RequestMethod.TOOL_CALL,
        params={
            "name": "slow_tool",
            "arguments": {}
        }
    )
    
    response = await server.handle_request(request)
    if response.result and response.result.get("isError"):
        if response.result.get("timedOut"):
            print(f"   ✓ Timeout handling successful")
            print(f"   Error: {response.result.get('result')}")
        else:
            print(f"   ✗ Unexpected error: {response.result.get('result')}")
    else:
        print(f"   ✗ Timeout test failed - tool should have timed out")
    
    # Test concurrent execution limits
    print("\n3. Testing concurrent execution limits...")
    
    # Create multiple tool calls
    async def medium_tool(arguments):
        """Tool that takes some time to execute."""
        await asyncio.sleep(1)
        return {"result": f"Processed {arguments}"}
    
    medium_tool_def = Tool(
        name="medium_tool",
        description="A tool that takes moderate time to execute",
        input_schema={"type": "object"}
    )
    
    server.register_tool(medium_tool_def, medium_tool)
    
    # Create multiple concurrent requests
    requests = []
    for i in range(5):
        request = MCPRequest(
            method=RequestMethod.TOOL_CALL,
            params={
                "name": "medium_tool",
                "arguments": {"task": f"task_{i}"}
            }
        )
        requests.append(request)
    
    # Execute all requests concurrently
    responses = await asyncio.gather(*[server.handle_request(req) for req in requests])
    
    # Count successful vs failed executions
    successful = sum(1 for r in responses if r.result and not r.result.get("isError"))
    failed = sum(1 for r in responses if r.result and r.result.get("isError"))
    
    print(f"   ✓ Concurrent execution test completed")
    print(f"   Successful executions: {successful}")
    print(f"   Failed executions (due to limits): {failed}")
    
    # Show final sandbox status
    print("\n4. Final sandbox status:")
    final_status = server.tool_sandbox.get_sandbox_status()
    print(f"   Active executions: {final_status['active_executions']}")
    
    print("\n=== Sandbox Demo Completed ===")


async def main():
    """Run the sandbox demo."""
    await demo_sandbox_features()


if __name__ == "__main__":
    asyncio.run(main())
