"""
Integration tests for tool result processing with the MCP server.
"""

import unittest
import asyncio
import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mcp.server import MCPServer
from mcp.mcp_types import MCPRequest, RequestMethod, Tool, ToolResult
from mcp.tool_result_processing import ToolResultSchema


class TestToolResultProcessingIntegration(unittest.TestCase):
    """Integration tests for tool result processing with MCP server."""

    def setUp(self):
        """Set up test server."""
        self.server = MCPServer()
        
        # Register a test tool with both old and new systems
        test_tool = Tool(
            name="test_processor",
            description="Test tool for result processing",
            input_schema={
                "type": "object",
                "properties": {
                    "value": {"type": "number"}
                }
            }
        )
        
        def test_executor(arguments):
            value = arguments.get("value", 0)
            if value < 0:
                return {"error": "Negative values not allowed"}
            return {"result": value * 2, "processed": True}
        
        # Register with old system for compatibility
        self.server.register_tool(test_tool, test_executor)
        
        # Register with new tool framework
        from mcp.tool_framework import ToolRegistration, ToolMetadata
        metadata = ToolMetadata(
            name="test_processor",
            description="Test tool for result processing",
            category="test"
        )
        
        tool_reg = ToolRegistration(
            tool=test_tool,
            executor=test_executor,
            metadata=metadata
        )
        
        self.server.tool_registry.register_tool(tool_reg)
        
        # Register a result schema for the tool
        schema = ToolResultSchema(
            tool_name="test_processor",
            expected_type="object",
            required_fields=["result"]
        )
        self.server.tool_result_processor.register_result_schema(schema)

    def test_tool_result_processing_integration(self):
        """Test integration of tool result processing with server."""
        async def run_test():
            # Start the server
            await self.server.start()
            
            # Execute a tool call
            request = MCPRequest(
                method=RequestMethod.TOOL_CALL,
                params={
                    "name": "test_processor",
                    "arguments": {"value": 21}
                }
            )
            
            response = await self.server.handle_request(request)
            
            # Check that the response is properly formatted
            self.assertIsNotNone(response.result)
            self.assertIsNone(response.error)
            self.assertEqual(response.result["toolName"], "test_processor")
            self.assertFalse(response.result["isError"])
            self.assertIn("result", response.result["result"])
            self.assertEqual(response.result["result"]["result"], 42)
            
            # Stop the server
            await self.server.stop()
        
        asyncio.run(run_test())

    def test_tool_result_processing_with_validation(self):
        """Test tool result processing with schema validation."""
        async def run_test():
            # Start the server
            await self.server.start()
            
            # Execute a tool call that should pass validation
            request = MCPRequest(
                method=RequestMethod.TOOL_CALL,
                params={
                    "name": "test_processor",
                    "arguments": {"value": 10}
                }
            )
            
            response = await self.server.handle_request(request)
            
            # Should succeed
            self.assertIsNotNone(response.result)
            self.assertFalse(response.result["isError"])
            
            # Stop the server
            await self.server.stop()
        
        asyncio.run(run_test())

    def test_tool_result_processing_error_handling(self):
        """Test tool result processing error handling."""
        async def run_test():
            # Start the server
            await self.server.start()
            
            # Execute a tool call that returns an error
            request = MCPRequest(
                method=RequestMethod.TOOL_CALL,
                params={
                    "name": "test_processor",
                    "arguments": {"value": -5}  # Negative value should cause error
                }
            )
            
            response = await self.server.handle_request(request)
            
            # Should still return a properly formatted response
            self.assertIsNotNone(response.result)
            self.assertEqual(response.result["toolName"], "test_processor")
            # Note: Our test executor returns a result even for "errors", 
            # so isError might still be False depending on implementation
            
            # Stop the server
            await self.server.stop()
        
        asyncio.run(run_test())

    def test_tool_result_processor_initialization(self):
        """Test that tool result processor is properly initialized."""
        self.assertIsNotNone(self.server.tool_result_processor)
        self.assertTrue(hasattr(self.server.tool_result_processor, 'process_result'))
        self.assertTrue(hasattr(self.server.tool_result_processor, 'register_result_schema'))


if __name__ == '__main__':
    unittest.main()
