"""
MCP (Model Context Protocol) package for the 4S1T Agent AI framework.

This package implements the Model Context Protocol specification for resource sharing,
tool integration, and context management between AI models and applications.

The MCP specification can be found at: https://modelcontextprotocol.io
"""

__version__ = "0.1.0"
__author__ = "4S1T Agent AI Team"

# Import main components
from .server import MCPServer, global_mcp_server
from .mcp_types import (
    MCPRequest,
    MCPResponse,
    Resource,
    Tool,
    Prompt,
    Configuration
)

__all__ = [
    "MCPServer",
    "global_mcp_server",
    "MCPRequest",
    "MCPResponse",
    "Resource",
    "Tool",
    "Prompt",
    "Configuration"
]
