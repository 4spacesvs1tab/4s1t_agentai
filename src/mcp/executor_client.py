"""
MCP Executor Client
Client for communicating with the sandboxed executor service.

This client:
1. Routes Python code execution to the executor service
2. Manages approval tokens
3. Handles errors and retries
4. Integrates with MCP tool framework
"""

import os
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from services.gateway.approval import ApprovalGateway, RiskLevel, ApprovalRequest

from utils.logger import setup_logger
logger = setup_logger(__name__)


class ExecutorConfig(BaseModel):
    """Configuration for executor client."""
    host: str = Field(default="executor", description="Executor service hostname")
    port: int = Field(default=8001, description="Executor service port")
    timeout: int = Field(default=60, description="Request timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    require_approval: bool = Field(default=True, description="Require approval for execution")
    
    @property
    def base_url(self) -> str:
        return f"http://self.host}:{self.port}"


@dataclass
class ExecutionResult:
    """Result of code execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    error_type: Optional[str] = None
    execution_time_ms: float = 0.0
    memory_usage_mb: float = 0.0
    approval_required: bool = False
    approval_request_id: Optional[str] = None


class ExecutorClient:
    """
    Client for the sandboxed executor service.
    
    Handles:
    - Approval workflow integration
    - Token generation
    - HTTP communication with executor
    - Error handling and retries
    """
    
    def __init__(
        self,
        config: Optional[ExecutorConfig] = None,
        approval_gateway: Optional[ApprovalGateway] = None
    ):
        """
        Initialize executor client.
        
        Args:
            config: Executor configuration
            approval_gateway: Approval gateway for generating tokens
        """
        self.config = config or ExecutorConfig()
        self.approval_gateway = approval_gateway
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout,
                follow_redirects=False
            )
        return self._client
    
    async def execute_code(
        self,
        code: str,
        user_id: str,
        approval_token: Optional[str] = None,
        timeout: int = 30
    ) -> ExecutionResult:
        """
        Execute Python code via the executor service.
        
        Args:
            code: Python code to execute
            user_id: User requesting execution
            approval_token: Pre-generated approval token (optional)
            timeout: Execution timeout in seconds
            
        Returns:
            ExecutionResult with stdout/stderr
        """
        # Step 1: Get approval token if not provided
        if approval_token is None and self.config.require_approval:
            if self.approval_gateway is None:
                return ExecutionResult(
                    success=False,
                    error="Approval required but approval gateway not configured",
                    approval_required=True
                )
            
            # Request approval
            approval_result = await self._request_approval(user_id, code)
            if not approval_result:
                return ExecutionResult(
                    success=False,
                    error="Approval denied or timed out",
                    approval_required=True
                )
            
            approval_token = approval_result.approval_token
        
        # Step 2: Send to executor
        return await self._send_to_executor(code, approval_token, timeout)
    
    async def _request_approval(
        self,
        user_id: str,
        code: str
    ) -> Optional[ApprovalRequest]:
        """
        Request approval for code execution.
        
        Args:
            user_id: User requesting execution
            code: Code to execute
            
        Returns:
            ApprovalRequest if approved, None otherwise
        """
        try:
            # Create approval request
            approval_req = await self.approval_gateway.request_code_execution_approval(
                user_id=user_id,
                code=code
            )
            
            logger.info(f"Approval requested: {approval_req.id}")
            
            # Wait for approval (polling)
            max_wait = 300  # 5 minutes
            poll_interval = 2  # 2 seconds
            waited = 0
            
            while waited < max_wait:
                # Check status
                approval_req = await self.approval_gateway.check_approval_status(
                    approval_req.id
                )
                
                if approval_req.status.value == "approved":
                    logger.info(f"Approval granted: {approval_req.id}")
                    return approval_req
                
                if approval_req.status.value == "denied":
                    logger.warning(f"Approval denied: {approval_req.id}")
                    return None
                
                if approval_req.status.value == "expired":
                    logger.warning(f"Approval expired: {approval_req.id}")
                    return None
                
                # Wait
                await asyncio.sleep(poll_interval)
                waited += poll_interval
            
            # Timeout
            logger.warning(f"Approval timeout: {approval_req.id}")
            return None
            
        except Exception as e:
            logger.error(f"Approval request failed: {e}")
            return None
    
    async def _send_to_executor(
        self,
        code: str,
        approval_token: str,
        timeout: int
    ) -> ExecutionResult:
        """
        Send code to executor service for execution.
        
        Args:
            code: Python code
            approval_token: Signed approval token
            timeout: Execution timeout
            
        Returns:
            ExecutionResult
        """
        client = await self._get_client()
        
        payload = {
            "code": code,
            "approval_token": approval_token,
            "timeout_seconds": timeout
        }
        
        url = f"self.config.base_url}/execute"
        
        try:
            response = await client.post(url, json=payload)
            
            if response.status_code == 200:
                data = response.json()
                return ExecutionResult(
                    success=data.get("success", False),
                    stdout=data.get("stdout", ""),
                    stderr=data.get("stderr", ""),
                    error=data.get("error"),
                    error_type=data.get("error_type"),
                    execution_time_ms=data.get("execution_time_ms", 0.0),
                    memory_usage_mb=data.get("memory_usage_mb", 0.0)
                )
            
            elif response.status_code == 401:
                return ExecutionResult(
                    success=False,
                    error="Invalid approval token",
                    error_type="AuthenticationError"
                )
            
            elif response.status_code == 400:
                data = response.json()
                return ExecutionResult(
                    success=False,
                    error=f"Code validation failed: {data.get('detail', 'Unknown error')}",
                    error_type="ValidationError"
                )
            
            else:
                return ExecutionResult(
                    success=False,
                    error=f"Executor error: {response.status_code}",
                    error_type="ExecutorError"
                )
                
        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to executor: {e}")
            return ExecutionResult(
                success=False,
                error="Executor service unavailable. Is the container running?",
                error_type="ConnectionError"
            )
        
        except httpx.TimeoutException:
            return ExecutionResult(
                success=False,
                error="Executor request timed out",
                error_type="TimeoutError"
            )
        
        except Exception as e:
            logger.error(f"Executor request failed: {e}")
            return ExecutionResult(
                success=False,
                error=str(e),
                error_type="RequestError"
            )
    
    async def get_capabilities(self) -> Dict[str, Any]:
        """Get executor capabilities."""
        client = await self._get_client()
        url = f"self.config.base_url}/capabilities"
        
        try:
            response = await client.get(url)
            if response.status_code == 200:
                return response.json()
            return {"error": f"HTTP {response.status_code}"}
        except Exception as e:
            logger.error(f"Failed to get capabilities: {e}")
            return {"error": str(e)}
    
    async def health_check(self) -> bool:
        """Check if executor is healthy."""
        client = await self._get_client()
        url = f"self.config.base_url}/health"
        
        try:
            response = await client.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get("status") == "healthy"
            return False
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None


# Global client instance
_executor_client: Optional[ExecutorClient] = None


def get_executor_client(
    approval_gateway: Optional[ApprovalGateway] = None
) -> ExecutorClient:
    """Get singleton executor client."""
    global _executor_client
    if _executor_client is None:
        # Load config from environment
        config = ExecutorConfig(
            host=os.getenv("EXECUTOR_HOST", "executor"),
            port=int(os.getenv("EXECUTOR_PORT", "8001")),
            timeout=int(os.getenv("EXECUTOR_TIMEOUT", "60")),
            require_approval=os.getenv("EXECUTOR_REQUIRE_APPROVAL", "true").lower() == "true"
        )
        _executor_client = ExecutorClient(config, approval_gateway)
    return _executor_client


# Import at end to avoid circular import
import asyncio
