"""
Tool integration framework for the 4S1T Agent AI system.

This module provides a framework for integrating and executing various tools
within the MCP (Model Context Protocol) server.
"""

import asyncio
import inspect
from typing import Any, Dict, List, Callable, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
import importlib
import pkgutil

from .mcp_types import Tool, ToolResult

from utils.logger import setup_logger
logger = setup_logger(__name__)


@dataclass
class ToolMetadata:
    """Metadata for a tool."""
    
    name: str
    description: str
    category: str = "general"
    version: str = "1.0.0"
    author: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class ToolRegistration:
    """Registration information for a tool."""
    
    tool: Tool
    executor: Callable
    metadata: ToolMetadata
    enabled: bool = True
    priority: int = 0  # Higher priority tools are preferred


class ToolRegistry:
    """Registry for managing tool registrations."""
    
    def __init__(self):
        self._tools: Dict[str, ToolRegistration] = {}
        self._categories: Dict[str, List[str]] = {}
        self.logger = logger
    
    def register_tool(self, tool_reg: ToolRegistration) -> bool:
        """
        Register a tool with the registry.
        
        Args:
            tool_reg: Tool registration information
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        try:
            # Store the tool registration
            self._tools[tool_reg.tool.name] = tool_reg
            
            # Update category mapping
            category = tool_reg.metadata.category
            if category not in self._categories:
                self._categories[category] = []
            if tool_reg.tool.name not in self._categories[category]:
                self._categories[category].append(tool_reg.tool.name)
            
            self.logger.info(f"Registered tool: {tool_reg.tool.name} (category: {category})")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register tool {tool_reg.tool.name}: {e}")
            return False
    
    def unregister_tool(self, tool_name: str) -> bool:
        """
        Unregister a tool from the registry.
        
        Args:
            tool_name: Name of the tool to unregister
            
        Returns:
            bool: True if unregistration was successful, False otherwise
        """
        try:
            if tool_name not in self._tools:
                return False
            
            # Remove from category mapping
            tool_reg = self._tools[tool_name]
            category = tool_reg.metadata.category
            if category in self._categories and tool_name in self._categories[category]:
                self._categories[category].remove(tool_name)
            
            # Remove the tool
            del self._tools[tool_name]
            
            self.logger.info(f"Unregistered tool: {tool_name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to unregister tool {tool_name}: {e}")
            return False
    
    def get_tool(self, tool_name: str) -> Optional[ToolRegistration]:
        """
        Get a tool registration by name.
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            ToolRegistration if found, None otherwise
        """
        return self._tools.get(tool_name)
    
    def list_tools(self, category: Optional[str] = None, enabled_only: bool = True) -> List[ToolRegistration]:
        """
        List all tools, optionally filtered by category and enabled status.
        
        Args:
            category: Category to filter by (None for all)
            enabled_only: Whether to only return enabled tools
            
        Returns:
            List of tool registrations
        """
        if category:
            tool_names = self._categories.get(category, [])
            tools = [self._tools[name] for name in tool_names if name in self._tools]
        else:
            tools = list(self._tools.values())
        
        if enabled_only:
            tools = [tool for tool in tools if tool.enabled]
        
        # Sort by priority (higher first)
        tools.sort(key=lambda x: x.priority, reverse=True)
        return tools
    
    def get_tool_names(self, category: Optional[str] = None, enabled_only: bool = True) -> List[str]:
        """
        Get list of tool names, optionally filtered by category and enabled status.
        
        Args:
            category: Category to filter by (None for all)
            enabled_only: Whether to only return enabled tools
            
        Returns:
            List of tool names
        """
        tools = self.list_tools(category, enabled_only)
        return [tool.tool.name for tool in tools]
    
    def enable_tool(self, tool_name: str) -> bool:
        """
        Enable a tool.
        
        Args:
            tool_name: Name of the tool to enable
            
        Returns:
            bool: True if successful, False otherwise
        """
        if tool_name in self._tools:
            self._tools[tool_name].enabled = True
            self.logger.info(f"Enabled tool: {tool_name}")
            return True
        return False
    
    def disable_tool(self, tool_name: str) -> bool:
        """
        Disable a tool.
        
        Args:
            tool_name: Name of the tool to disable
            
        Returns:
            bool: True if successful, False otherwise
        """
        if tool_name in self._tools:
            self._tools[tool_name].enabled = False
            self.logger.info(f"Disabled tool: {tool_name}")
            return True
        return False
    
    def get_categories(self) -> List[str]:
        """
        Get list of all categories.
        
        Returns:
            List of category names
        """
        return list(self._categories.keys())


class ToolExecutor:
    """Executor for running tools with proper error handling and result processing."""
    
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.logger = logger
    
    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """
        Execute a tool with the given arguments.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments for the tool
            
        Returns:
            ToolResult with execution results
        """
        try:
            # Get tool registration
            tool_reg = self.registry.get_tool(tool_name)
            if not tool_reg:
                return ToolResult(
                    tool_name=tool_name,
                    result=f"Tool not found: {tool_name}",
                    is_error=True
                )
            
            if not tool_reg.enabled:
                return ToolResult(
                    tool_name=tool_name,
                    result=f"Tool is disabled: {tool_name}",
                    is_error=True
                )
            
            # Validate arguments against schema
            validation_result = self._validate_arguments(tool_reg.tool, arguments)
            if not validation_result.valid:
                return ToolResult(
                    tool_name=tool_name,
                    result=f"Invalid arguments: {validation_result.error}",
                    is_error=True
                )
            
            # Execute the tool
            self.logger.debug(f"Executing tool: {tool_name} with args: {arguments}")
            
            # Check if executor is async or sync
            if inspect.iscoroutinefunction(tool_reg.executor):
                result = await tool_reg.executor(arguments)
            else:
                # Run sync function in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, tool_reg.executor, arguments)
            
            self.logger.debug(f"Tool {tool_name} executed successfully")
            
            return ToolResult(
                tool_name=tool_name,
                result=result,
                is_error=False
            )
            
        except Exception as e:
            self.logger.error(f"Error executing tool {tool_name}: {e}")
            return ToolResult(
                tool_name=tool_name,
                result=str(e),
                is_error=True
            )
    
    def _validate_arguments(self, tool: Tool, arguments: Dict[str, Any]) -> object:
        """
        Validate tool arguments against the tool's input schema.
        
        Args:
            tool: Tool definition
            arguments: Arguments to validate
            
        Returns:
            Validation result object with valid flag and error message
        """
        # Simple validation result class
        class ValidationResult:
            def __init__(self, valid: bool, error: str = ""):
                self.valid = valid
                self.error = error
        
        # For now, we'll do basic validation
        # In a full implementation, we would use a JSON schema validator
        schema = tool.input_schema
        
        if not schema:
            return ValidationResult(True)  # No schema to validate against
        
        # Check required properties
        required_props = schema.get("required", [])
        for prop in required_props:
            if prop not in arguments:
                return ValidationResult(False, f"Missing required argument: {prop}")
        
        return ValidationResult(True)


class ToolDiscovery:
    """Discover and load tools from modules."""
    
    @staticmethod
    def discover_tools_from_module(module_name: str) -> List[ToolRegistration]:
        """
        Discover tools from a module.
        
        Args:
            module_name: Name of the module to scan
            
        Returns:
            List of discovered tool registrations
        """
        try:
            module = importlib.import_module(module_name)
            tools = []
            
            # Look for tool functions in the module
            for name in dir(module):
                attr = getattr(module, name)
                if callable(attr) and hasattr(attr, '_tool_metadata'):
                    # This is a tool function
                    metadata = getattr(attr, '_tool_metadata')
                    
                    # Create Tool object
                    tool = Tool(
                        name=metadata.get('name', name),
                        description=metadata.get('description', ''),
                        input_schema=metadata.get('input_schema', {}),
                        output_schema=metadata.get('output_schema')
                    )
                    
                    # Create ToolMetadata
                    tool_metadata = ToolMetadata(
                        name=tool.name,
                        description=tool.description,
                        category=metadata.get('category', 'general'),
                        version=metadata.get('version', '1.0.0'),
                        author=metadata.get('author', ''),
                        tags=metadata.get('tags', [])
                    )
                    
                    # Create ToolRegistration
                    tool_reg = ToolRegistration(
                        tool=tool,
                        executor=attr,
                        metadata=tool_metadata
                    )
                    
                    tools.append(tool_reg)
            
            return tools
        except Exception as e:
            logger.error(f"Failed to discover tools from module {module_name}: {e}")
            return []


def tool(name: str, description: str = "", category: str = "general", 
         input_schema: Dict[str, Any] = None, output_schema: Dict[str, Any] = None,
         version: str = "1.0.0", author: str = "", tags: List[str] = None):
    """
    Decorator for registering functions as tools.
    
    Args:
        name: Tool name
        description: Tool description
        category: Tool category
        input_schema: JSON schema for input validation
        output_schema: JSON schema for output description
        version: Tool version
        author: Tool author
        tags: Tool tags
    """
    def decorator(func):
        # Attach metadata to the function
        func._tool_metadata = {
            'name': name,
            'description': description,
            'category': category,
            'input_schema': input_schema or {},
            'output_schema': output_schema,
            'version': version,
            'author': author,
            'tags': tags or []
        }
        return func
    return decorator


# Example tool functions
@tool(
    name="calculator",
    description="Performs basic arithmetic operations",
    category="math",
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
def calculator_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Example calculator tool."""
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


@tool(
    name="echo",
    description="Echoes back the input text",
    category="utility",
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string"}
        },
        "required": ["text"]
    }
)
async def echo_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Example async echo tool."""
    text = arguments.get("text", "")
    return {"echo": text, "length": len(text)}


# Example usage
if __name__ == "__main__":
    # Create registry and register example tools
    registry = ToolRegistry()
    
    # Register calculator tool
    calc_metadata = ToolMetadata(
        name="calculator",
        description="Performs basic arithmetic operations",
        category="math"
    )
    
    calc_tool = Tool(
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
    
    calc_registration = ToolRegistration(
        tool=calc_tool,
        executor=calculator_tool,
        metadata=calc_metadata
    )
    
    registry.register_tool(calc_registration)
    
    # List tools
    print("Registered tools:")
    for tool_reg in registry.list_tools():
        print(f"  - {tool_reg.tool.name}: {tool_reg.tool.description}")
    
    # Test tool execution
    executor = ToolExecutor(registry)
    
    # Execute calculator tool
    async def test_execution():
        result = await executor.execute_tool("calculator", {
            "operation": "add",
            "a": 5,
            "b": 3
        })
        print(f"Calculator result: {result}")
    
    asyncio.run(test_execution())
