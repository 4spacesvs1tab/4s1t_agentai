"""
Python Executor MCP Tool
Secure Python code execution via sandboxed executor service.

This tool routes all code execution through the air-gapped executor service
with approval workflow.
"""

from typing import Dict, Any, Optional

from mcp.tool_framework import Tool, ToolParameter, ToolMetadata
from mcp.executor_client import get_executor_client, ExecutionResult
from services.gateway.approval import get_approval_gateway
from utils.dlp_scanner import get_code_security_scanner

from utils.logger import setup_logger
logger = setup_logger(__name__)


class PythonExecutorTool(Tool):
    """
    Tool for executing Python code in sandboxed environment.
    
    Requires:
    1. Code validation (forbidden patterns)
    2. DLP scanning (no secrets in code)
    3. Risk assessment
    4. User approval via Authy push (for medium+ risk)
    5. Execution in air-gapped container
    
    Security: All code runs in isolated container with no network/filesystem access.
    """
    
    def __init__(self):
        """Initialize Python executor tool."""
        super().__init__()
        self.executor_client = None
        self.code_scanner = get_code_security_scanner()
    
    @property
    def metadata(self) -> ToolMetadata:
        """Tool metadata."""
        return ToolMetadata(
            name="execute_python",
            description="Execute Python code in sandboxed environment. "
                       "Code runs in isolated container with restricted resources. "
                       "Requires approval for execution.",
            version="1.0.0",
            author="4S1T Agent AI Security Team",
            requires_approval=True,
            risk_level="medium"
        )
    
    def get_parameters(self) -> Dict[str, ToolParameter]:
        """Define tool parameters."""
        return {
            "code": ToolParameter(
                name="code",
                description="Python code to execute",
                type="string",
                required=True,
                max_length=10000,
                validation_pattern=r"^[\s\S]{1,10000}$"
            ),
            "timeout": ToolParameter(
                name="timeout",
                description="Execution timeout in seconds (1-60)",
                type="integer",
                required=False,
                default=30,
                min_value=1,
                max_value=60
            ),
            "context": ToolParameter(
                name="context",
                description="Optional context about what the code does",
                type="string",
                required=False,
                max_length=500
            )
        }
    
    async def initialize(self) -> None:
        """Initialize the tool."""
        # Get approval gateway
        approval_gateway = get_approval_gateway()
        
        # Create executor client
        self.executor_client = get_executor_client(approval_gateway)
        
        logger.info("Python executor tool initialized")
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute Python code securely.
        
        Args:
            arguments: {
                "code": "Python code to execute",
                "timeout": 30,  # optional
                "context": "description"  # optional
            }
            
        Returns:
            {
                "success": bool,
                "stdout": str,
                "stderr": str,
                "result": Any,
                "error": str (if failed)
            }
        """
        code = arguments.get("code", "")
        timeout = arguments.get("timeout", 30)
        context = arguments.get("context", "")
        
        # Step 1: Validate code for forbidden patterns
        is_safe, violations = self.code_scanner.scan_code(code)
        if not is_safe:
            logger.warning(f"Code security scan failed: {violations}")
            return {
                "success": False,
                "error": f"Code contains forbidden patterns: {', '.join(violations[:3])}",
                "stdout": "",
                "stderr": "",
                "result": None
            }
        
        # Step 2: Check if executor is healthy
        if not await self.executor_client.health_check():
            logger.error("Executor service is not healthy")
            return {
                "success": False,
                "error": "Executor service unavailable. Please check Docker containers.",
                "stdout": "",
                "stderr": "",
                "result": None
            }
        
        # Step 3: Get current user (from context or session)
        user_id = arguments.get("_user_id", "anonymous")
        
        # Step 4: Execute via executor service
        try:
            result: ExecutionResult = await self.executor_client.execute_code(
                code=code,
                user_id=user_id,
                timeout=timeout
            )
            
            # Step 5: Return results
            return {
                "success": result.success,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "result": result.error if result.error else None,
                "execution_time_ms": result.execution_time_ms,
                "memory_usage_mb": result.memory_usage_mb,
                "error": result.error if not result.success else None,
                "approval_required": result.approval_required,
                "approval_request_id": result.approval_request_id
            }
            
        except Exception as e:
            logger.error(f"Python execution failed: {e}")
            return {
                "success": False,
                "error": f"Execution failed: {str(e)}",
                "stdout": "",
                "stderr": "",
                "result": None
            }
    
    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.executor_client:
            await self.executor_client.close()
        logger.info("Python executor tool cleaned up")


# Tool registration function
def register_python_executor_tool(registry):
    """Register the Python executor tool."""
    tool = PythonExecutorTool()
    registry.register_tool("execute_python", tool)
    logger.info("Python executor tool registered")
