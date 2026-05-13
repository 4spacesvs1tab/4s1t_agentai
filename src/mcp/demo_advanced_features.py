"""
Demo script showcasing the new MCP capabilities including context serialization
and advanced tool result processing.
"""

import asyncio
import json
import sys
import os
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.server import MCPServer
from mcp.mcp_types import MCPRequest, RequestMethod, Tool, Resource
from mcp.context_serialization import serialize_context, deserialize_context, compress_context, decompress_context
from mcp.tool_result_processing import ToolResultProcessor, ToolResultSchema, ToolResult
from mcp.tool_framework import ToolRegistration, ToolMetadata, tool


async def demo_context_serialization():
    """Demonstrate context serialization capabilities."""
    print("=== Context Serialization Demo ===")
    
    # Create sample context
    context = {
        "timestamp": datetime.now(),
        "agent_state": {
            "current_task": "processing_user_request",
            "memory_usage": 0.75,
            "tools_available": ["calculator", "web_search"],
            "recent_interactions": [
                {"type": "user_input", "content": "Calculate 21 * 2"},
                {"type": "tool_call", "tool": "calculator", "args": {"operation": "multiply", "a": 21, "b": 2}}
            ]
        },
        "resource_cache": {
            "file:///example.txt": {
                "content": "Example content for demonstration",
                "last_accessed": datetime.now()
            }
        }
    }
    
    # Serialize to JSON
    json_serialized = serialize_context(context, "json")
    print("JSON Serialized Context:")
    print(json_serialized[:200] + "..." if len(json_serialized) > 200 else json_serialized)
    print()
    
    # Deserialize from JSON
    json_deserialized = deserialize_context(json_serialized, "json")
    print("Deserialized Context Keys:", list(json_deserialized.keys()))
    print()
    
    # Compress context
    compressed = compress_context(context)
    print(f"Original size: {len(json_serialized)} bytes")
    print(f"Compressed size: {len(compressed)} bytes")
    print(f"Compression ratio: {len(compressed) / len(json_serialized):.2%}")
    print()
    
    # Decompress context
    decompressed = decompress_context(compressed)
    print("Decompressed Context Keys:", list(decompressed.keys()))
    print()


async def demo_tool_result_processing():
    """Demonstrate tool result processing capabilities."""
    print("=== Tool Result Processing Demo ===")
    
    # Create processor
    processor = ToolResultProcessor()
    
    # Register a schema for validation
    calc_schema = ToolResultSchema(
        tool_name="calculator",
        expected_type="object",
        required_fields=["result"],
        min_value=0  # Assuming positive results only
    )
    processor.register_result_schema(calc_schema)
    
    # Process a successful result
    success_result = ToolResult(
        tool_name="calculator",
        result={"result": 42, "operation": "multiply", "expression": "21 * 2"},
        is_error=False
    )
    
    processed = processor.process_result(success_result)
    print("Processed Success Result:")
    print(processed.formatted_output)
    print()
    
    # Process an error result
    error_result = ToolResult(
        tool_name="calculator",
        result="Division by zero error",
        is_error=True
    )
    
    processed_error = processor.process_result(error_result)
    print("Processed Error Result:")
    print(processed_error.formatted_output)
    print()
    
    # Transform a result
    transformed = processor.transform_result(processed, "json")
    print("Transformed Result (JSON format):")
    print(transformed.formatted_output)
    print()
    
    # Show validation errors
    invalid_result = ToolResult(
        tool_name="calculator",
        result={"value": 42},  # Missing required "result" field
        is_error=False
    )
    
    processed_invalid = processor.process_result(invalid_result)
    print("Processed Invalid Result (with validation errors):")
    print("Is Error:", processed_invalid.is_error)
    if processed_invalid.validation_errors:
        print("Validation Errors:", processed_invalid.validation_errors)
    print()


@tool(
    name="demo_calculator",
    description="Demonstration calculator tool",
    category="demo",
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
async def demo_calculator(arguments):
    """Demo calculator tool."""
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
    
    return {"result": result, "operation": operation, "expression": f"{a} {operation} {b}"}


async def demo_mcp_server():
    """Demonstrate MCP server with new capabilities."""
    print("=== MCP Server Demo ===")
    
    # Create server
    server = MCPServer()
    await server.start()
    
    # Add valid tokens for different services
    server.add_valid_token("admin-service-token", {"read", "write", "admin"})
    server.add_valid_token("monitoring-token", {"read"})
    server.add_valid_token("automation-token", {"read", "write"})
    
    # Register demo tool using decorator
    # The decorator automatically registers the tool
    
    # Register the same tool with the tool framework for full integration
    demo_tool = Tool(
        name="demo_calculator",
        description="Demonstration calculator tool",
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
    
    metadata = ToolMetadata(
        name="demo_calculator",
        description="Demonstration calculator tool",
        category="demo"
    )
    
    tool_reg = ToolRegistration(
        tool=demo_tool,
        executor=demo_calculator,
        metadata=metadata
    )
    
    server.tool_registry.register_tool(tool_reg)
    
    # Register a resource
    example_resource = Resource(
        uri="file:///demo.txt",
        name="Demo Resource",
        description="A demonstration resource",
        mime_type="text/plain"
    )
    server.register_resource(example_resource)
    
    # Test authentication with valid token
    print("Testing authentication with valid token...")
    auth_result = server.authenticate_client("admin_service", "admin-service-token")
    print(f"Authentication result: {auth_result}")
    
    if auth_result:
        has_admin = server.has_client_permission("admin_service", "admin")
        print(f"Admin service has admin permission: {has_admin}")
    
    # Test tool call
    tool_request = MCPRequest(
        method=RequestMethod.TOOL_CALL,
        params={
            "name": "demo_calculator",
            "arguments": {
                "operation": "multiply",
                "a": 21,
                "b": 2
            }
        }
    )
    
    print("\nSending tool call request...")
    response = await server.handle_request(tool_request)
    
    if response.result:
        print("Tool Call Response:")
        print(json.dumps(response.result, indent=2))
    else:
        print("Tool Call Error:")
        print(json.dumps(response.error, indent=2))
    
    print()
    
    # Test resource list
    resource_request = MCPRequest(method=RequestMethod.RESOURCE_LIST)
    print("Sending resource list request...")
    response = await server.handle_request(resource_request)
    
    if response.result:
        print("Resource List Response:")
        print(json.dumps(response.result, indent=2))
    
    await server.stop()
    print()


async def main():
    """Run all demos."""
    print("🚀 4S1T Agent AI - MCP Advanced Capabilities Demo")
    print("=" * 50)
    print()
    
    # Run context serialization demo
    await demo_context_serialization()
    
    # Run tool result processing demo
    await demo_tool_result_processing()
    
    # Run MCP server demo
    await demo_mcp_server()
    
    print("✅ Demo completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
