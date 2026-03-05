"""
System initializer for the 4S1T Agent AI framework.

Handles application startup, component initialization, and graceful shutdown.
"""
import asyncio
import logging
from typing import Dict, Any, List, Callable, Awaitable
from contextlib import asynccontextmanager

from config.settings import Settings
from utils.logger import setup_logger

logger = setup_logger(__name__)


class SystemInitializer:
    """Manages system initialization and lifecycle."""
    
    def __init__(self, settings: Settings = None):
        """
        Initialize the system initializer.
        
        Args:
            settings: Application settings instance
        """
        self.settings = settings or Settings()
        self._initialized_components: List[str] = []
        self._shutdown_callbacks: List[Callable[[], Awaitable[None]]] = []
        self._is_initialized = False
        
    async def initialize_component(self, name: str, init_func: Callable[[], Awaitable[None]]) -> None:
        """
        Initialize a component with error handling.
        
        Args:
            name: Name of the component
            init_func: Async function to initialize the component
        """
        try:
            logger.info(f"Initializing component: {name}")
            await init_func()
            self._initialized_components.append(name)
            logger.info(f"Component {name} initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize component {name}: {str(e)}")
            raise
    
    def register_shutdown_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """
        Register a callback to be called during shutdown.
        
        Args:
            callback: Async function to call during shutdown
        """
        self._shutdown_callbacks.append(callback)
    
    async def initialize_all(self) -> None:
        """Initialize all system components."""
        if self._is_initialized:
            logger.warning("System already initialized")
            return
            
        logger.info("Starting system initialization...")
        
        # Load startup configuration
        from components.config.loader import load_startup_config
        startup_config = load_startup_config()
        logger.info(f"Loaded startup configuration from sources: {startup_config.get('_sources', 'default')}")
        
        # Initialize database tables
        from services.auth_service import get_auth_service
        auth_service = get_auth_service()
        auth_service.initialize_database()

        # Run numbered migrations (idempotent — safe to call on every startup).
        # importlib is required because module names starting with digits are not
        # valid Python identifiers and cannot be imported with the `import` statement.
        import importlib
        for _migration_module in (
            "database.migrations.006_add_model_preferences",
            "database.migrations.007_add_apikey_model_override",
        ):
            _mod = importlib.import_module(_migration_module)
            _mod.run_migration()
        
        # Initialize vector database collections
        from vector_database.service import get_vector_database_service
        vector_db_service = get_vector_database_service()
        vector_db_service.initialize_collections()
        
        # Initialize MCP server and register tools
        await self._initialize_mcp_server()
        
        # Start event bus processing
        from components.events.event_bus import get_event_bus
        event_bus = get_event_bus()
        await event_bus.start_processing()
        
        # Start health monitoring
        from components.health.monitor import get_health_monitor
        health_monitor = get_health_monitor()
        await health_monitor.start_monitoring()
        
        self._is_initialized = True
        logger.info("System initialization complete")
    
    async def _initialize_mcp_server(self) -> None:
        """Initialize MCP server and register tools."""
        try:
            from mcp.server import MCPServer
            from mcp.mcp_types import Tool
            from mcp.chat_tool import chat_tool_executor
            from mcp.tool_framework import ToolRegistration, ToolMetadata
            
            # Import existing tool executors
            from mcp.server import example_calculator_executor, example_web_search_executor
            
            logger.info("Initializing MCP server...")
            
            # Create MCP server instance
            mcp_server = MCPServer()
            
            # Start the server
            await mcp_server.start()
            
            # Register chat tool using the new tool framework
            chat_tool = Tool(
                name="chat",
                description="Conversational AI chat tool powered by Nano-GPT models",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "The user's message to respond to"},
                        "model": {"type": "string", "description": "Model to use for response", "default": "glm-4.6"},
                        "temperature": {"type": "number", "description": "Response creativity (0.0-1.0)", "default": 0.7},
                        "max_tokens": {"type": "integer", "description": "Maximum response length", "default": 500}
                    },
                    "required": ["message"]
                }
            )
            
            # Create ToolRegistration for chat tool
            chat_tool_metadata = ToolMetadata(
                name="chat",
                description="Conversational AI chat tool powered by Nano-GPT models",
                category="ai",
                version="1.0.0",
                author="4S1T Agent AI Team",
                tags=["ai", "chat", "nano-gpt"]
            )
            
            chat_tool_reg = ToolRegistration(
                tool=chat_tool,
                executor=chat_tool_executor,
                metadata=chat_tool_metadata,
                enabled=True,
                priority=10  # High priority for main chat tool
            )
            
            # Register in both old and new systems for compatibility
            mcp_server.register_tool(chat_tool, chat_tool_executor)  # Old system
            mcp_server.tool_registry.register_tool(chat_tool_reg)  # New system
            logger.info("Registered chat tool with MCP server")
            
            # Register existing calculator tool
            from mcp.server import calculator_tool
            
            calc_tool_metadata = ToolMetadata(
                name="calculator",
                description="Performs basic arithmetic operations",
                category="utility",
                version="1.0.0",
                author="4S1T Agent AI Team",
                tags=["math", "calculator", "utility"]
            )
            
            calc_tool_reg = ToolRegistration(
                tool=calculator_tool,
                executor=example_calculator_executor,
                metadata=calc_tool_metadata,
                enabled=True,
                priority=5
            )
            
            mcp_server.register_tool(calculator_tool, example_calculator_executor)  # Old system
            mcp_server.tool_registry.register_tool(calc_tool_reg)  # New system
            logger.info("Registered calculator tool with MCP server")
            
            # Register existing web search tool
            from mcp.server import web_search_tool
            
            web_search_metadata = ToolMetadata(
                name="web_search",
                description="Searches the web for information",
                category="web",
                version="1.0.0",
                author="4S1T Agent AI Team",
                tags=["web", "search", "information"]
            )
            
            web_search_reg = ToolRegistration(
                tool=web_search_tool,
                executor=example_web_search_executor,
                metadata=web_search_metadata,
                enabled=True,
                priority=5
            )
            
            mcp_server.register_tool(web_search_tool, example_web_search_executor)  # Old system
            mcp_server.tool_registry.register_tool(web_search_reg)  # New system
            logger.info("Registered web search tool with MCP server")
            
            # Store reference for later use
            from mcp import server
            server.global_mcp_server = mcp_server
            
            logger.info("MCP server initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize MCP server: {str(e)}")
            # Don't raise the exception to avoid breaking the entire system
            pass
    
    async def shutdown_all(self) -> None:
        """Shutdown all system components gracefully."""
        logger.info("Starting system shutdown...")
        
        # Stop health monitoring
        try:
            from components.health.monitor import get_health_monitor
            health_monitor = get_health_monitor()
            await health_monitor.stop_monitoring()
        except Exception as e:
            logger.error(f"Error stopping health monitor: {str(e)}")
        
        # Stop event bus processing
        try:
            from components.events.event_bus import get_event_bus
            event_bus = get_event_bus()
            await event_bus.stop_processing()
        except Exception as e:
            logger.error(f"Error stopping event bus: {str(e)}")
        
        # Call all registered shutdown callbacks in reverse order
        for callback in reversed(self._shutdown_callbacks):
            try:
                await callback()
            except Exception as e:
                logger.error(f"Error during shutdown callback: {str(e)}")
        
        logger.info("System shutdown complete")
        self._is_initialized = False


@asynccontextmanager
async def system_lifespan(initializer: SystemInitializer):
    """
    Context manager for system lifecycle management.
    
    Args:
        initializer: SystemInitializer instance
    """
    try:
        await initializer.initialize_all()
        yield
    finally:
        await initializer.shutdown_all()


# Global system initializer instance
system_initializer = SystemInitializer()
