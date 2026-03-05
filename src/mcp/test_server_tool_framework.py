"""
Integration tests for the MCP server with the new tool framework.
"""

import unittest
import asyncio
import sys
import os
from datetime import datetime

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mcp.server import MCPServer
from mcp.mcp_types import (
    MCPRequest, RequestMethod, Tool, Prompt, Resource,
    ClientCapabilities, InitializeParams
)
from mcp.tool_framework import ToolRegistry, ToolExecutor, ToolRegistration, ToolMetadata


class TestMCPServerWithToolFramework(unittest.TestCase):
    """Integration tests for MCP server with tool framework."""

    def setUp(self):
        """Set up test server."""
        self.server = MCPServer()
        
        # Register a test tool using the new framework
        test_tool = Tool(
            name="test_calculator",
            description="Test calculator tool",
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["add", "subtract"]},
                    "a": {"type": "number"},
                    "b": {"type": "number"}
                },
                "required": ["operation", "a", "b"]
            }
        )
        
        def test_executor(arguments):
            operation = arguments.get("operation")
            a = arguments.get("a", 0)
            b = arguments.get("b", 0)
            
            if operation == "add":
                return {"result": a + b}
            elif operation == "subtract":
                return {"result": a - b}
            else:
                raise ValueError(f"Unknown operation: {operation}")
        
        # Register with both old and new systems for compatibility
        self.server.register_tool(test_tool, test_executor)
        
        # Also register with the new tool framework
        metadata = ToolMetadata(
            name="test_calculator",
            description="Test calculator tool",
            category="math"
        )
        
        tool_reg = ToolRegistration(
            tool=test_tool,
            executor=test_executor,
            metadata=metadata
        )
        
        self.server.tool_registry.register_tool(tool_reg)

    def test_server_initialization(self):
        """Test that server initializes correctly with tool framework."""
        self.assertFalse(self.server.running)
        self.assertIsInstance(self.server.tool_registry, ToolRegistry)
        self.assertIsInstance(self.server.tool_executor, ToolExecutor)

    def test_tool_registration(self):
        """Test tool registration with both systems."""
        # Check old system
        self.assertIn("test_calculator", self.server.tools)
        self.assertIn("test_calculator", self.server.tool_executors)
        
        # Check new system
        registered_tool = self.server.tool_registry.get_tool("test_calculator")
        self.assertIsNotNone(registered_tool)
        self.assertEqual(registered_tool.tool.name, "test_calculator")

    def test_tool_execution_via_new_framework(self):
        """Test tool execution using the new framework."""
        async def run_test():
            result = await self.server.tool_executor.execute_tool("test_calculator", {
                "operation": "add",
                "a": 10,
                "b": 5
            })
            
            self.assertFalse(result.is_error)
            self.assertEqual(result.tool_name, "test_calculator")
            self.assertEqual(result.result["result"], 15)
        
        asyncio.run(run_test())

    def test_tool_call_handler(self):
        """Test the tool call handler with new framework."""
        async def run_test():
            # Start the server
            await self.server.start()
            
            # Create a tool call request
            request = MCPRequest(
                method=RequestMethod.TOOL_CALL,
                params={
                    "name": "test_calculator",
                    "arguments": {
                        "operation": "subtract",
                        "a": 10,
                        "b": 3
                    }
                }
            )
            
            # Handle the request
            response = await self.server.handle_request(request)
            
            # Check the response
            self.assertIsNotNone(response.result)
            self.assertIsNone(response.error)
            self.assertEqual(response.result["toolName"], "test_calculator")
            self.assertEqual(response.result["result"]["result"], 7)
            self.assertFalse(response.result["isError"])
            
            # Stop the server
            await self.server.stop()
        
        asyncio.run(run_test())

    def test_tool_call_with_invalid_tool(self):
        """Test tool call with non-existent tool."""
        async def run_test():
            # Start the server
            await self.server.start()
            
            # Create a tool call request for non-existent tool
            request = MCPRequest(
                method=RequestMethod.TOOL_CALL,
                params={
                    "name": "non_existent_tool",
                    "arguments": {}
                }
            )
            
            # Handle the request
            response = await self.server.handle_request(request)
            
            # Check the response
            self.assertIsNotNone(response.result)
            self.assertEqual(response.result["toolName"], "non_existent_tool")
            self.assertTrue(response.result["isError"])
            self.assertIn("not found", response.result["result"])
            
            # Stop the server
            await self.server.stop()
        
        asyncio.run(run_test())

    def test_tool_list_handler(self):
        """Test the tool list handler."""
        async def run_test():
            # Start the server
            await self.server.start()
            
            # Create a tool list request
            request = MCPRequest(method=RequestMethod.TOOL_LIST)
            
            # Handle the request
            response = await self.server.handle_request(request)
            
            # Check the response
            self.assertIsNotNone(response.result)
            self.assertIn("tools", response.result)
            self.assertGreater(len(response.result["tools"]), 0)
            
            # Find our test tool in the list
            test_tools = [t for t in response.result["tools"] if t["name"] == "test_calculator"]
            self.assertEqual(len(test_tools), 1)
            self.assertEqual(test_tools[0]["description"], "Test calculator tool")
            
            # Stop the server
            await self.server.stop()
        
        asyncio.run(run_test())


if __name__ == '__main__':
    unittest.main()
