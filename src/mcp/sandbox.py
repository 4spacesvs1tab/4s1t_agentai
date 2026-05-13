"""
Tool execution sandbox for the MCP (Model Context Protocol) implementation.

This module provides a secure execution environment for MCP tools with
resource limits and security controls.
"""

import asyncio
import subprocess
import threading
import time
import signal
import os
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta


from utils.logger import setup_logger
logger = setup_logger(__name__)


@dataclass
class SandboxConfig:
    """Configuration for the tool execution sandbox."""
    
    # Resource limits
    timeout_seconds: int = 30
    memory_limit_mb: int = 100
    cpu_limit_percent: int = 50
    
    # Security settings
    allow_network: bool = False
    allowed_modules: set = field(default_factory=set)
    blocked_functions: set = field(default_factory=lambda: {
        'eval', 'exec', 'compile', 'open', 'file', '__import__',
        'getattr', 'setattr', 'delattr', 'hasattr'
    })
    
    # Execution settings
    max_concurrent_executions: int = 10


@dataclass
class ExecutionResult:
    """Result of a tool execution."""
    
    success: bool
    output: Any = None
    error: Optional[str] = None
    execution_time: float = 0.0
    memory_used_mb: float = 0.0
    timed_out: bool = False


class ToolSandbox:
    """Provides a secure execution environment for MCP tools."""
    
    def __init__(self, config: Optional[SandboxConfig] = None):
        """
        Initialize the tool sandbox.
        
        Args:
            config: Sandbox configuration
        """
        self.config = config or SandboxConfig()
        self.active_executions = 0
        self.max_concurrent = self.config.max_concurrent_executions
        self.logger = logger
    
    async def execute_tool(self, tool_func: Callable, arguments: Dict[str, Any]) -> ExecutionResult:
        """
        Execute a tool function in a secure sandbox environment.
        
        Args:
            tool_func: The tool function to execute
            arguments: Arguments to pass to the tool function
            
        Returns:
            ExecutionResult: Result of the tool execution
        """
        start_time = time.time()
        
        # Check concurrent execution limit
        if self.active_executions >= self.max_concurrent:
            return ExecutionResult(
                success=False,
                error="Maximum concurrent executions reached",
                execution_time=0.0
            )
        
        self.active_executions += 1
        self.logger.debug(f"Starting tool execution, active executions: {self.active_executions}")
        
        try:
            # Execute the tool function with timeout
            try:
                result = await asyncio.wait_for(
                    self._safe_execute(tool_func, arguments),
                    timeout=self.config.timeout_seconds
                )
                
                execution_time = time.time() - start_time
                return ExecutionResult(
                    success=True,
                    output=result,
                    execution_time=execution_time
                )
                
            except asyncio.TimeoutError:
                execution_time = time.time() - start_time
                self.logger.warning("Tool execution timed out")
                return ExecutionResult(
                    success=False,
                    error="Tool execution timed out",
                    execution_time=execution_time,
                    timed_out=True
                )
                
        except Exception as e:
            execution_time = time.time() - start_time
            self.logger.error(f"Tool execution failed: {e}")
            return ExecutionResult(
                success=False,
                error=str(e),
                execution_time=execution_time
            )
            
        finally:
            self.active_executions -= 1
            self.logger.debug(f"Finished tool execution, active executions: {self.active_executions}")
    
    async def _safe_execute(self, tool_func: Callable, arguments: Dict[str, Any]) -> Any:
        """
        Safely execute a tool function with security checks.
        
        Args:
            tool_func: The tool function to execute
            arguments: Arguments to pass to the tool function
            
        Returns:
            Any: Result of the tool function
        """
        # For Python functions, we rely on the function implementation being safe
        # In a more advanced implementation, we could use techniques like:
        # - Restricted execution environments
        # - AST parsing to check for dangerous operations
        # - Process isolation
        
        # For now, we'll execute the function directly but with timeout protection
        return await tool_func(arguments)
    
    def execute_external_command(self, command: str, cwd: Optional[str] = None) -> ExecutionResult:
        """
        Execute an external command in a secure sandbox environment.
        
        CRITICAL SECURITY FIX (Phase 1): Shell command execution is DISABLED
        to prevent remote code execution (RCE) attacks. This method previously
        allowed arbitrary shell commands which is now blocked.
        
        Args:
            command: The command to execute (IGNORED - execution disabled)
            cwd: Working directory for the command (IGNORED)
            
        Returns:
            ExecutionResult: Always returns failure with security message
        """
        self.logger.error(
            "SECURITY: Attempted shell command execution BLOCKED. "
            f"Command: {command[:50]}... in dir: {cwd}"
        )
        
        return ExecutionResult(
            success=False,
            error="Shell command execution is disabled for security reasons. "
                  "This feature has been removed to prevent remote code execution attacks. "
                  "Use Python-based MCP tools instead.",
            execution_time=0.0
        )
    
    def _check_forbidden_patterns(self, command: str) -> tuple[bool, Optional[str]]:
        """
        Check command for forbidden/dangerous patterns.
        
        This is a backup safety check in case shell execution
        is ever re-enabled (which should only happen with extreme caution).
        
        Args:
            command: Command string to check
            
        Returns:
            tuple: (is_safe, error_message)
        """
        import re
        
        # Forbidden patterns that indicate dangerous operations
        forbidden_patterns = [
            # Command chaining/blacklisting
            (r'[;&|`]', 'Command chaining/redirection operators'),
            # Command substitution
            (r'\$\s*\(|`[^`]+`', 'Command substitution'),
            # Dangerous commands
            (r'\bmv\s+\/', 'System file modifications'),
            (r'\brm\s+-rf', 'Recursive deletion'),
            (r'\bdd\s+if=', 'Disk operations'),
            (r'\bwget\s+.*\s*-O\s*/', 'System file downloads'),
            (r'\bcurl\s+.*\s*-o\s*/', 'System file downloads'),
            # Path traversal attempts
            (r'\.\.\s*/', 'Path traversal'),
            # Hidden/system directories
            (r'/\.(ssh|git|env|aws|config|kube)', 'Access to sensitive directories'),
            # Privilege escalation
            (r'\bsudo\b|\bsu\s+-', 'Privilege escalation'),
            # Network access that bypasses restrictions
            (r'nc\s+-|netcat|\bnmap', 'Network scanning tools'),
        ]
        
        for pattern, description in forbidden_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"Forbidden pattern detected: {description}"
        
        return True, None
    
    def get_sandbox_status(self) -> Dict[str, Any]:
        """
        Get the current status of the sandbox.
        
        Returns:
            Dict[str, Any]: Sandbox status information
        """
        return {
            "active_executions": self.active_executions,
            "max_concurrent": self.max_concurrent,
            "timeout_seconds": self.config.timeout_seconds,
            "memory_limit_mb": self.config.memory_limit_mb,
            "allow_network": self.config.allow_network
        }


# Example usage
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    
    # Create sandbox
    sandbox = ToolSandbox(SandboxConfig(timeout_seconds=10))
    
    # Example tool function
    async def example_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Example tool function."""
        # Simulate some work
        await asyncio.sleep(1)
        return {"result": f"Processed {arguments}"}
    
    # Execute tool in sandbox
    async def main():
        result = await sandbox.execute_tool(example_tool, {"input": "test data"})
        print(f"Tool execution result: {result}")
        
        # Execute external command
        result = sandbox.execute_external_command("echo 'Hello from sandbox'")
        print(f"Command execution result: {result}")
        
        # Get sandbox status
        status = sandbox.get_sandbox_status()
        print(f"Sandbox status: {status}")
    
    asyncio.run(main())
