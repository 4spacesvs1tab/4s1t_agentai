"""
Secure file operations for MCP tools with path validation.

This module wraps file operations with security controls to prevent:
- Directory traversal attacks
- Access to sensitive files
- Large file reads (DoS)
"""

import os
from pathlib import Path
from typing import Optional, Union, Dict, Any
import logging

from utils.path_validation import validate_path, validate_file_size
from services.exceptions import PermissionError as ServicePermissionError

logger = logging.getLogger(__name__)

# Default workspace directory for MCP file operations.
# Intentionally scoped to a 'workspace' subdirectory so that MCP tools
# cannot traverse up into the service source tree, .env files, or DB files.
# Override by setting MCP_ALLOWED_PATH in the environment.
_DEFAULT_WORKSPACE = Path(os.getenv("MCP_ALLOWED_PATH", "")).resolve() or (
    Path.cwd() / "workspace"
).resolve()
ALLOWED_BASE_PATH: Path = _DEFAULT_WORKSPACE
# Ensure the workspace directory exists at import time so the path is usable.
ALLOWED_BASE_PATH.mkdir(parents=True, exist_ok=True)

# Maximum file size for reads (10MB default)
MAX_FILE_SIZE = int(os.getenv("MCP_MAX_FILE_SIZE", 10 * 1024 * 1024))


def read_file(file_path: Union[str, Path], 
              allowed_base_path: Optional[Path] = None,
              max_size: int = MAX_FILE_SIZE) -> Dict[str, Any]:
    """
    Securely read a file with path validation.
    
    Args:
        file_path: The user-provided file path
        allowed_base_path: Base path for access restrictions
        max_size: Maximum allowed file size
        
    Returns:
        Dict with 'success', 'content', and 'error' keys
        
    Raises:
        ServicePermissionError: If access is denied
    """
    try:
        # Use default base path if not provided
        base_path = allowed_base_path or ALLOWED_BASE_PATH
        
        # Validate path (checks traversal, forbidden patterns)
        validated_path = validate_path(file_path, base_path)
        
        # Check file exists
        if not validated_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if not validated_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")
        
        # Validate file size
        validate_file_size(validated_path, max_size)
        
        # Log the access for audit purposes
        logger.info(f"File read: {validated_path}")
        
        # Read content
        content = validated_path.read_text(encoding='utf-8')
        
        return {
            "success": True,
            "content": content,
            "path": str(validated_path),
            "size": len(content)
        }
        
    except PermissionError as e:
        logger.warning(f"File access denied: {file_path} - {e}")
        raise ServicePermissionError(str(e))
    except FileNotFoundError as e:
        logger.warning(f"File not found: {file_path}")
        raise
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        raise


def write_file(file_path: Union[str, Path], 
               content: str,
               allowed_base_path: Optional[Path] = None,
               max_size: int = MAX_FILE_SIZE) -> Dict[str, Any]:
    """
    Securely write a file with path validation.
    
    Args:
        file_path: The user-provided file path
        content: Content to write
        allowed_base_path: Base path for access restrictions
        max_size: Maximum allowed file size
        
    Returns:
        Dict with 'success', 'path', and 'error' keys
        
    Raises:
        ServicePermissionError: If access is denied
    """
    try:
        # Use default base path if not provided
        base_path = allowed_base_path or ALLOWED_BASE_PATH
        
        # Validate path (checks traversal, forbidden patterns)
        validated_path = validate_path(file_path, base_path)
        
        # Check content size
        content_bytes = content.encode('utf-8')
        if len(content_bytes) > max_size:
            raise ValueError(
                f"Content too large ({len(content_bytes)/1024/1024:.2f}MB > {max_size/1024/1024}MB)"
            )
        
        # Ensure parent directory exists
        validated_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write content
        validated_path.write_text(content, encoding='utf-8')
        
        # Log the write for audit purposes
        logger.info(f"File written: {validated_path}")
        
        return {
            "success": True,
            "path": str(validated_path),
            "size": len(content_bytes)
        }
        
    except PermissionError as e:
        logger.warning(f"File write denied: {file_path} - {e}")
        raise ServicePermissionError(str(e))
    except Exception as e:
        logger.error(f"Error writing file {file_path}: {e}")
        raise


def list_directory(dir_path: Union[str, Path] = ".",
                   allowed_base_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Securely list directory contents with path validation.
    
    Args:
        dir_path: The user-provided directory path
        allowed_base_path: Base path for access restrictions
        
    Returns:
        Dict with 'success', 'items', and 'path' keys
        
    Raises:
        ServicePermissionError: If access is denied
    """
    try:
        # Use default base path if not provided
        base_path = allowed_base_path or ALLOWED_BASE_PATH
        
        # Validate path (checks traversal, forbidden patterns)
        validated_path = validate_path(dir_path, base_path)
        
        # Check directory exists
        if not validated_path.exists():
            raise FileNotFoundError(f"Directory not found: {dir_path}")
        
        if not validated_path.is_dir():
            raise ValueError(f"Path is not a directory: {dir_path}")
        
        # List contents
        items = []
        for item in validated_path.iterdir():
            item_info = {
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None
            }
            items.append(item_info)
        
        # Log the access for audit purposes
        logger.info(f"Directory listed: {validated_path}")
        
        return {
            "success": True,
            "path": str(validated_path),
            "items": items
        }
        
    except PermissionError as e:
        logger.warning(f"Directory access denied: {dir_path} - {e}")
        raise ServicePermissionError(str(e))
    except FileNotFoundError as e:
        logger.warning(f"Directory not found: {dir_path}")
        raise
    except Exception as e:
        logger.error(f"Error listing directory {dir_path}: {e}")
        raise


def file_exists(file_path: Union[str, Path],
                allowed_base_path: Optional[Path] = None) -> bool:
    """
    Check if a file exists with path validation.
    
    Args:
        file_path: The user-provided file path
        allowed_base_path: Base path for access restrictions
        
    Returns:
        bool: True if file exists and is accessible
    """
    try:
        base_path = allowed_base_path or ALLOWED_BASE_PATH
        validated_path = validate_path(file_path, base_path)
        return validated_path.exists() and validated_path.is_file()
    except (PermissionError, ValueError):
        return False


def get_allowed_base_path() -> Path:
    """Get the configured allowed base path."""
    return ALLOWED_BASE_PATH
