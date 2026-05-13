"""
Tests for the tool framework functionality.
"""

import unittest
import asyncio
import sys
import os
from typing import Dict, Any

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mcp.tool_framework import (
    ToolRegistry, ToolExecutor, ToolRegistration, ToolMetadata, Tool,
    tool, calculator_tool, echo_tool
)


class TestToolFramework(unittest.TestCase):
    """Test cases for the tool framework."""

    def setUp(self):
        """Set up test data."""
        self.registry = ToolRegistry()
        
        # Create test tool
        self.test_tool = Tool(
            name="test_tool",
            description="A test tool",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string"}
                },
                "required": ["message"]
            }
        )
        
        self.test_metadata = ToolMetadata(
            name="test_tool",
            description="A test tool",
            category="testing"
        )
        
        def test_executor(arguments: Dict[str, Any]) -> Dict[str, Any]:
            return {"received": arguments.get("message", ""), "status": "success"}
        
        self.test_registration = ToolRegistration(
            tool=self.test_tool,
            executor=test_executor,
            metadata=self.test_metadata
        )

    def test_tool_registry_register_tool(self):
        """Test registering a tool."""
        result = self.registry.register_tool(self.test_registration)
        self.assertTrue(result)
        
        # Check that tool is registered
        registered_tool = self.registry.get_tool("test_tool")
        self.assertIsNotNone(registered_tool)
        self.assertEqual(registered_tool.tool.name, "test_tool")

    def test_tool_registry_unregister_tool(self):
        """Test unregistering a tool."""
        # First register the tool
        self.registry.register_tool(self.test_registration)
        
        # Then unregister it
        result = self.registry.unregister_tool("test_tool")
        self.assertTrue(result)
        
        # Check that tool is no longer registered
        registered_tool = self.registry.get_tool("test_tool")
        self.assertIsNone(registered_tool)

    def test_tool_registry_list_tools(self):
        """Test listing tools."""
        # Register test tool
        self.registry.register_tool(self.test_registration)
        
        # Register calculator tool
        calc_metadata = ToolMetadata(
            name="calculator",
            description="Performs basic arithmetic operations",
            category="math"
        )
        
        calc_tool = Tool(
            name="calculator",
            description="Performs basic arithmetic operations"
        )
        
        def calc_executor(arguments: Dict[str, Any]) -> Dict[str, Any]:
            return {"result": "calculated"}
        
        calc_registration = ToolRegistration(
            tool=calc_tool,
            executor=calc_executor,
            metadata=calc_metadata
        )
        
        self.registry.register_tool(calc_registration)
        
        # Test listing all tools
        all_tools = self.registry.list_tools()
        self.assertEqual(len(all_tools), 2)
        
        # Test listing by category
        math_tools = self.registry.list_tools(category="math")
        self.assertEqual(len(math_tools), 1)
        self.assertEqual(math_tools[0].tool.name, "calculator")

    def test_tool_registry_enable_disable_tool(self):
        """Test enabling and disabling tools."""
        # Register tool
        self.registry.register_tool(self.test_registration)
        
        # Initially should be enabled
        tool_reg = self.registry.get_tool("test_tool")
        self.assertTrue(tool_reg.enabled)
        
        # Disable tool
        result = self.registry.disable_tool("test_tool")
        self.assertTrue(result)
        
        tool_reg = self.registry.get_tool("test_tool")
        self.assertFalse(tool_reg.enabled)
        
        # Enable tool
        result = self.registry.enable_tool("test_tool")
        self.assertTrue(result)
        
        tool_reg = self.registry.get_tool("test_tool")
        self.assertTrue(tool_reg.enabled)

    def test_tool_executor_execute_sync_tool(self):
        """Test executing a synchronous tool."""
        # Register tool
        self.registry.register_tool(self.test_registration)
        
        # Create executor
        executor = ToolExecutor(self.registry)
        
        # Execute tool
        async def run_test():
            result = await executor.execute_tool("test_tool", {"message": "Hello, World!"})
            self.assertFalse(result.is_error)
            self.assertEqual(result.tool_name, "test_tool")
            self.assertIn("received", result.result)
            self.assertEqual(result.result["received"], "Hello, World!")
        
        asyncio.run(run_test())

    def test_tool_executor_execute_async_tool(self):
        """Test executing an asynchronous tool."""
        # Register echo tool
        echo_metadata = ToolMetadata(
            name="echo",
            description="Echoes back the input text",
            category="utility"
        )
        
        echo_tool_obj = Tool(
            name="echo",
            description="Echoes back the input text",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"}
                },
                "required": ["text"]
            }
        )
        
        echo_registration = ToolRegistration(
            tool=echo_tool_obj,
            executor=echo_tool,
            metadata=echo_metadata
        )
        
        self.registry.register_tool(echo_registration)
        
        # Create executor
        executor = ToolExecutor(self.registry)
        
        # Execute tool
        async def run_test():
            result = await executor.execute_tool("echo", {"text": "Test message"})
            self.assertFalse(result.is_error)
            self.assertEqual(result.tool_name, "echo")
            self.assertIn("echo", result.result)
            self.assertEqual(result.result["echo"], "Test message")
        
        asyncio.run(run_test())

    def test_tool_executor_tool_not_found(self):
        """Test executing a non-existent tool."""
        executor = ToolExecutor(self.registry)
        
        async def run_test():
            result = await executor.execute_tool("non_existent_tool", {})
            self.assertTrue(result.is_error)
            self.assertIn("not found", result.result)
        
        asyncio.run(run_test())

    def test_tool_executor_disabled_tool(self):
        """Test executing a disabled tool."""
        # Register and disable tool
        self.registry.register_tool(self.test_registration)
        self.registry.disable_tool("test_tool")
        
        # Create executor
        executor = ToolExecutor(self.registry)
        
        async def run_test():
            result = await executor.execute_tool("test_tool", {"message": "Hello"})
            self.assertTrue(result.is_error)
            self.assertIn("disabled", result.result)
        
        asyncio.run(run_test())

    def test_tool_decorator(self):
        """Test the tool decorator."""
        # The calculator_tool should have metadata attached
        self.assertTrue(hasattr(calculator_tool, '_tool_metadata'))
        
        metadata = getattr(calculator_tool, '_tool_metadata')
        self.assertEqual(metadata['name'], 'calculator')
        self.assertEqual(metadata['category'], 'math')
        self.assertIn('operation', str(metadata['input_schema']))

    def test_tool_registry_get_categories(self):
        """Test getting tool categories."""
        # Register tools in different categories
        self.registry.register_tool(self.test_registration)
        
        categories = self.registry.get_categories()
        self.assertIn("testing", categories)


if __name__ == '__main__':
    unittest.main()
