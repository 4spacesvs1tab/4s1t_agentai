"""
Sandboxed Executor Service
Air-gapped code execution service with minimal attack surface.

This service:
1. Receives code and approval tokens via HTTP
2. Verifies token authenticity
3. Executes code in restricted Python environment
4. Returns only stdout/stderr (no network, no FS access)
5. Runs as non-root user in read-only container

Security features:
- No network access (Docker network_mode: none)
- Read-only filesystem
- Non-root user (UID 65534)
- Resource limits (128MB RAM, 0.5 CPU, 30s timeout)
- No subprocess execution
- Restricted Python builtins
"""

import ast
import hashlib
import logging
import resource
import signal
import sys
import threading
import time
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.executor.security import verify_approval_token, verify_code_hash, SecurityError

# ---------------------------------------------------------------------------
# Single-use JTI (JWT ID) store — prevents approval token replay attacks.
# Stores {jti: expiry_unix_timestamp}. Expired entries are pruned on each use.
# Thread-safe; in-process only (appropriate for the air-gapped executor).
# ---------------------------------------------------------------------------
_used_jtis: Dict[str, float] = {}
_jti_lock = threading.Lock()


def _consume_jti(jti: str, expires_at: float) -> bool:
    """
    Record a JTI as consumed.  Returns True on first use, False if already seen.
    Also purges expired entries to bound memory growth.
    """
    now = time.time()
    with _jti_lock:
        # Prune entries whose tokens have already expired
        expired = [k for k, exp in _used_jtis.items() if exp < now]
        for k in expired:
            del _used_jtis[k]

        if jti in _used_jtis:
            return False  # Already consumed

        _used_jtis[jti] = expires_at
        return True

# Configure logging (to stderr since stdout is captured)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sandboxed Executor Service",
    description="Air-gapped code execution with approval verification",
    version="1.0.0"
)


# =============================================================================
# Request/Response Models
# =============================================================================

class CodeExecutionRequest(BaseModel):
    """Request model for code execution."""
    code: str = Field(..., min_length=1, max_length=10000, description="Python code to execute")
    approval_token: str = Field(..., description="Signed JWT approval token from main service")
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique execution ID")
    timeout_seconds: int = Field(default=30, ge=1, le=60, description="Execution timeout (max 60s)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "code": "x = 1 + 1\nprint(x)",
                "approval_token": "eyJhbGciOiJFUzI1NiIs...",
                "execution_id": "550e8400-e29b-41d4-a716-446655440000",
                "timeout_seconds": 30
            }
        }


class CodeExecutionResponse(BaseModel):
    """Response model for code execution."""
    execution_id: str
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_value: Optional[Any] = None
    execution_time_ms: float
    memory_usage_mb: float
    error: Optional[str] = None
    error_type: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "execution_id": "550e8400-e29b-41d4-a716-446655440000",
                "success": True,
                "stdout": "2\n",
                "stderr": "",
                "return_value": None,
                "execution_time_ms": 45.2,
                "memory_usage_mb": 12.3
            }
        }


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    timestamp: str


# =============================================================================
# Security Restrictions
# =============================================================================

FORBIDDEN_MODULES = {
    'os', 'sys', 'subprocess', 'socket', 'urllib', 'http', 'ftplib', 
    'smtplib', 'telnetlib', 'xmlrpc', 'pickle', 'shelve', 'dbm',
    'sqlite3', 'zlib', 'gzip', 'bz2', 'lzma', 'zipfile', 'tarfile',
    'shutil', 'pathlib', 'path', 'tempfile', 'fileinput', 'linecache',
    'crypt', 'spwd', 'grp', 'pwd', 'spwd', 'resource', 'posix',
    'nt', 'ctypes', 'mmap', 'fcntl', 'termios', 'tty', 'pty',
    'multiprocessing', 'concurrent', 'threading', '_thread',
    'asyncio', 'select', 'selectors', 'signal', 'atexit',
}

FORBIDDEN_BUILTINS = {
    'open', 'eval', 'exec', 'compile', '__import__', 'input',
    'raw_input', 'reload', 'file', 'execfile', 'memoryview',
}

FORBIDDEN_NAMES = {
    '__builtins__', '__import__', '__loader__', '__spec__',
    '__package__', '__file__', '__cached__', '__doc__',
}


class RestrictedPythonVisitor(ast.NodeVisitor):
    """AST visitor to detect forbidden Python constructs."""
    
    def __init__(self):
        self.violations: List[str] = []
    
    def visit_Import(self, node):
        """Check imports."""
        for alias in node.names:
            module_name = alias.name.split('.')[0]
            if module_name in FORBIDDEN_MODULES:
                self.violations.append(f"Forbidden import: {alias.name}")
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node):
        """Check from imports."""
        if node.module:
            module_name = node.module.split('.')[0]
            if module_name in FORBIDDEN_MODULES:
                self.violations.append(f"Forbidden import from: {node.module}")
        self.generic_visit(node)
    
    def visit_Name(self, node):
        """Check names."""
        if node.id in FORBIDDEN_NAMES:
            self.violations.append(f"Forbidden name: {node.id}")
        self.generic_visit(node)
    
    def visit_Call(self, node):
        """Check function calls."""
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_BUILTINS:
                self.violations.append(f"Forbidden builtin call: {node.func.id}")
            if node.func.id in FORBIDDEN_MODULES:
                self.violations.append(f"Forbidden module call: {node.func.id}")
        self.generic_visit(node)
    
    def visit_Attribute(self, node):
        """Check attribute access."""
        if node.attr in FORBIDDEN_BUILTINS:
            self.violations.append(f"Forbidden attribute access: {node.attr}")
        self.generic_visit(node)
    
    def visit_Exec(self, node):
        """Block exec statements (Python 2)."""
        self.violations.append("exec statement is forbidden")
        self.generic_visit(node)


def validate_code(code: str) -> Tuple[bool, List[str]]:
    """
    Validate code for forbidden patterns.
    
    Args:
        code: Python code to validate
        
    Returns:
        (is_valid, violations)
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"Syntax error: {e}"]
    
    visitor = RestrictedPythonVisitor()
    visitor.visit(tree)
    
    return len(visitor.violations) == 0, visitor.violations


def create_restricted_globals() -> Dict[str, Any]:
    """
    Create restricted globals dictionary.
    
    Returns:
        Dictionary of allowed builtins and modules
    """
    import builtins
    
    # Start with empty dict
    restricted_globals = {}
    
    # Add safe builtins.
    # '__import__' is intentionally excluded — keeping it would allow
    # sandbox escape via __import__('os'), __import__('subprocess'), etc.
    # '__build_class__' is kept so class definitions work normally.
    safe_builtins = {
        'abs', 'all', 'any', 'ascii', 'bin', 'bool', 'bytearray', 'bytes',
        'callable', 'chr', 'classmethod', 'complex', 'delattr', 'dict',
        'dir', 'divmod', 'enumerate', 'filter', 'float', 'format',
        'frozenset', 'getattr', 'globals', 'hasattr', 'hash', 'help',
        'hex', 'id', 'int', 'isinstance', 'issubclass', 'iter', 'len',
        'list', 'locals', 'map', 'max', 'min', 'next', 'object', 'oct',
        'ord', 'pow', 'print', 'property', 'range', 'repr', 'reversed',
        'round', 'set', 'setattr', 'slice', 'sorted', 'staticmethod',
        'str', 'sum', 'super', 'tuple', 'type', 'vars', 'zip',
        '__build_class__', '__name__', 'True', 'False', 'None',
    }
    
    # Build restricted builtins dict
    restricted_builtins = {}
    for name in safe_builtins:
        if hasattr(builtins, name):
            restricted_builtins[name] = getattr(builtins, name)
    
    restricted_globals['__builtins__'] = restricted_builtins
    
    # Add safe modules (limited functionality)
    import math
    import random
    import statistics
    import itertools
    import functools
    import collections
    import decimal
    import fractions
    import numbers
    import datetime
    import time
    import json
    import re
    import string
    
    restricted_globals['math'] = math
    restricted_globals['random'] = random
    restricted_globals['statistics'] = statistics
    restricted_globals['itertools'] = itertools
    restricted_globals['functools'] = functools
    restricted_globals['collections'] = collections
    restricted_globals['decimal'] = decimal
    restricted_globals['fractions'] = fractions
    restricted_globals['numbers'] = numbers
    restricted_globals['datetime'] = datetime
    restricted_globals['time'] = time
    restricted_globals['json'] = json
    restricted_globals['re'] = re
    restricted_globals['string'] = string
    
    return restricted_globals


def set_resource_limits():
    """Set resource limits for the process."""
    try:
        # Limit CPU time (seconds) - only in Docker container
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    except (ValueError, OSError):
        pass  # Not supported on macOS or in current environment
    
    try:
        # Limit memory (128MB soft, 256MB hard) - may fail on macOS
        soft_limit = 128 * 1024 * 1024
        hard_limit = 256 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (soft_limit, hard_limit))
    except (ValueError, OSError):
        pass  # Not supported on macOS
    
    try:
        # Limit number of processes
        resource.setrlimit(resource.RLIMIT_NPROC, (10, 10))
    except (ValueError, OSError):
        pass
    
    try:
        # Limit file descriptors
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except (ValueError, OSError):
        pass
    
    try:
        # Limit stack size
        resource.setrlimit(resource.RLIMIT_STACK, (8 * 1024 * 1024, 8 * 1024 * 1024))
    except (ValueError, OSError):
        pass


def execute_restricted(code: str, timeout: int = 30) -> Dict[str, Any]:
    """
    Execute code in restricted environment.
    
    Args:
        code: Python code to execute
        timeout: Maximum execution time in seconds
        
    Returns:
        Execution result dictionary
    """
    import time as time_module
    
    start_time = time_module.time()
    start_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    
    stdout_capture = StringIO()
    stderr_capture = StringIO()
    
    result = {
        'success': False,
        'stdout': '',
        'stderr': '',
        'return_value': None,
        'execution_time_ms': 0.0,
        'memory_usage_mb': 0.0,
        'error': None,
        'error_type': None,
    }
    
    def timeout_handler(signum, frame):
        raise TimeoutError(f"Code execution exceeded {timeout} seconds")
    
    try:
        # Set timeout
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)
        
        # Set resource limits
        set_resource_limits()
        
        # Create restricted globals
        restricted_globals = create_restricted_globals()
        restricted_locals = {}
        
        # Capture stdout/stderr
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            # Compile and execute
            compiled = compile(code, '<sandbox>', 'exec', optimize=0)
            exec(compiled, restricted_globals, restricted_locals)
            
            # Get return value (last expression if any)
            if restricted_locals:
                # Try to get the last assigned value
                result['return_value'] = list(restricted_locals.values())[-1]
        
        result['success'] = True
        
    except TimeoutError as e:
        result['error'] = str(e)
        result['error_type'] = 'TimeoutError'
    except Exception as e:
        result['error'] = str(e)
        result['error_type'] = type(e).__name__
        result['stderr'] = traceback.format_exc()
    finally:
        # Reset alarm and handler
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        
        # Calculate metrics
        end_time = time_module.time()
        end_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        
        result['execution_time_ms'] = (end_time - start_time) * 1000
        result['memory_usage_mb'] = (end_memory - start_memory) / 1024
        result['stdout'] = stdout_capture.getvalue()
        result['stderr'] = stderr_capture.getvalue() + result.get('stderr', '')
    
    return result


# =============================================================================
# API Endpoints
# =============================================================================

@app.post("/execute", response_model=CodeExecutionResponse)
async def execute_code(request: CodeExecutionRequest):
    """
    Execute Python code in sandboxed environment.
    
    Requires a valid approval token signed by the main service.
    """
    logger.info(f"Execution request received: {request.execution_id}")

    # Step 1: Verify approval token signature and claims
    try:
        token_data = verify_approval_token(request.approval_token)
        logger.info(f"Token verified for execution: {request.execution_id}")
    except SecurityError as e:
        logger.error(f"Token verification failed: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid approval token: {e}")

    # Step 2: Enforce single-use — prevent replay of the same approval token
    jti = token_data.get("jti")
    if not jti:
        logger.error("Approval token missing JTI claim")
        raise HTTPException(status_code=401, detail="Approval token missing required JTI claim")

    token_exp = token_data.get("exp", 0)
    if not _consume_jti(jti, float(token_exp)):
        logger.warning(f"Replay attempt: JTI {jti} already consumed (execution_id={request.execution_id})")
        raise HTTPException(status_code=401, detail="Approval token has already been used")

    # Step 3: Verify the submitted code matches what was approved
    # The approval token carries a SHA-256 hash of the approved code;
    # executing different code than was approved is a security violation.
    approval_data = token_data.get("approval", {})
    expected_code_hash = approval_data.get("code_hash")
    if expected_code_hash and not verify_code_hash(request.code, expected_code_hash):
        logger.error(
            f"Code hash mismatch for execution {request.execution_id}: "
            f"submitted code does not match approved code"
        )
        raise HTTPException(
            status_code=400,
            detail="Submitted code does not match the approved code (hash mismatch)"
        )

    # Step 4: AST-level static analysis — block forbidden constructs
    is_valid, violations = validate_code(request.code)
    if not is_valid:
        logger.warning(f"Code validation failed: {violations}")
        raise HTTPException(
            status_code=400,
            detail=f"Code contains forbidden patterns: {', '.join(violations)}"
        )

    # Step 5: Execute in restricted sandbox
    logger.info(f"Executing code: {request.execution_id}")
    result = execute_restricted(request.code, request.timeout_seconds)
    
    # Step 4: Return response
    return CodeExecutionResponse(
        execution_id=request.execution_id,
        success=result['success'],
        stdout=result['stdout'],
        stderr=result['stderr'],
        return_value=result['return_value'],
        execution_time_ms=result['execution_time_ms'],
        memory_usage_mb=result['memory_usage_mb'],
        error=result['error'],
        error_type=result['error_type']
    )


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    from datetime import datetime, timezone
    
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        timestamp=datetime.now(timezone.utc).isoformat()
    )


@app.get("/capabilities")
async def get_capabilities():
    """Return available capabilities and restrictions."""
    return {
        "python_version": "3.11",
        "allowed_modules": [
            "math", "random", "statistics", "itertools", "functools",
            "collections", "decimal", "fractions", "numbers",
            "datetime", "time", "json", "re", "string"
        ],
        "forbidden_modules": list(FORBIDDEN_MODULES),
        "forbidden_builtins": list(FORBIDDEN_BUILTINS),
        "max_execution_time": 60,
        "max_memory_mb": 128,
        "network_access": False,
        "filesystem_access": False,
    }


# =============================================================================
# Error Handlers
# =============================================================================

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Handle unexpected errors."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc) if isinstance(exc, HTTPException) else "An unexpected error occurred"
        }
    )


# =============================================================================
# Startup
# =============================================================================

@app.on_event("startup")
async def startup_event():
    """Run on service startup."""
    logger.info("=" * 60)
    logger.info("Sandboxed Executor Service Starting")
    logger.info("Security: Non-root user, no network, read-only FS")
    logger.info("Restrictions: 128MB RAM, 0.5 CPU, 60s timeout")
    logger.info("=" * 60)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, workers=1)
