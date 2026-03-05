"""
Path validation utilities with security hardening.

Provides functions to validate and sanitize file paths to prevent:
- Directory traversal attacks
- Access to sensitive files
- Large file reads
- Self-modification (blocking writes to src/)
- Access to personal/financial data
"""

import os
import re
import logging
from pathlib import Path
from typing import Union, List, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PathValidationConfig:
    """Configuration for path validation."""
    # Whitelist of allowed directories (empty = allow any within base)
    whitelisted_dirs: List[Path] = None
    # Blacklist of forbidden directories/files
    blacklisted_patterns: List[str] = None
    # Protect source code from modification
    protect_source: bool = True
    # Source directory to protect
    source_dir: Path = None
    # Block access to personal/financial data
    block_personal_data: bool = True
    # Require approval for file reads
    require_approval: bool = False
    
    def __post_init__(self):
        if self.whitelisted_dirs is None:
            self.whitelisted_dirs = []
        if self.blacklisted_patterns is None:
            self.blacklisted_patterns = []
        if self.source_dir is None:
            # Auto-detect source directory
            self.source_dir = Path(__file__).parent.parent


# Global configuration
_path_config: Optional[PathValidationConfig] = None


def get_path_config() -> PathValidationConfig:
    """Get global path validation configuration."""
    global _path_config
    if _path_config is None:
        _path_config = PathValidationConfig()
    return _path_config


def set_path_config(config: PathValidationConfig):
    """Set global path validation configuration."""
    global _path_config
    _path_config = config


# Comprehensive forbidden patterns
FORBIDDEN_PATTERNS = {
    # System and secrets
    'system': [
        '.ssh', '.gnupg', '.aws', '.azure', '.gcp', '.docker',
        'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'id_ed25519_sk',
        '.bash_history', '.zsh_history', '.fish_history',
        '.netrc', '.pgpass', '.my.cnf', '.git-credentials',
    ],
    # Secrets and keys
    'secrets': [
        '.env', '.env.local', '.env.production', '.env.development',
        'password', 'secret', 'private_key', 'privatekey',
        'api_key', 'apikey', 'auth_token', 'access_token',
        'credentials', 'keystore', 'wallet', 'seed',
    ],
    # Personal data
    'personal': [
        'Documents/personal', 'Documents/financial', 'Documents/tax',
        'Documents/bank', 'Downloads/statement', 'Downloads/invoice',
        'Pictures/private', 'Pictures/identity',
    ],
    # Bitcoin/Crypto
    'crypto': [
        'wallet.dat', 'bitcoin', 'electrum', 'ledger', 'trezor',
        'metamask', 'seed.txt', 'recovery_phrase', 'mnemonic',
    ],
    # Nostr
    'nostr': [
        'nsec', 'nostr_keys', '.nsec', 'nostr.json', 'keys.json',
    ],
    # Financial
    'financial': [
        'credit_card', 'cc_number', 'ssn', 'social_security',
        'passport', 'id_card', 'bank_account', 'iban',
    ],
    # System directories
    'system_dirs': [
        '/etc', '/var', '/root', '/home/*/.config',
        '/proc', '/sys', '/dev', '/boot', '/usr/local/etc',
    ],
    # Agent self-modification
    'self_mod': [
        '__pycache__', '.pyc', '.pyo', '.pyd',
    ],
}


# Patterns that trigger CRITICAL (immediate block)
CRITICAL_PATTERNS = [
    '.ssh/', '.gnupg/', 'id_rsa', '.env', 'wallet.dat',
    'nsec', 'seed.txt', 'password.txt', 'secret.key',
    'credentials.json', 'api_key', 'private_key',
]


def validate_path(user_path: Union[str, Path],
                 allowed_base_path: Union[str, Path] = None,
                 access_type: str = 'read') -> Path:
    """
    Validate and sanitize file paths with comprehensive security checks.
    
    Args:
        user_path: The user-provided path to validate
        allowed_base_path: Base path that all access must be contained within
        access_type: Type of access ('read', 'write', 'execute')
            
    Returns:
        Resolved, absolute Path object within allowed base
        
    Raises:
        PermissionError: If path is outside allowed directory or matches forbidden pattern
        FileNotFoundError: If path doesn't exist
        ValueError: If validation fails
    """
    config = get_path_config()
    
    # Set default base path
    if allowed_base_path is None:
        allowed_base_path = Path.cwd()
    elif isinstance(allowed_base_path, str):
        allowed_base_path = Path(allowed_base_path)
    allowed_base_path = allowed_base_path.resolve()

    # Convert to Path object and resolve
    if isinstance(user_path, str):
        requested_path = Path(user_path).expanduser()
    else:
        requested_path = user_path.expanduser()
    requested_path = requested_path.resolve()

    # Check 1: Absolute path traversal protection
    try:
        requested_path.relative_to(allowed_base_path)
    except ValueError:
        logger.warning(f"Path traversal attempt: '{user_path}' outside '{allowed_base_path}'")
        raise PermissionError(
            f"Access denied: Path '{user_path}' is outside allowed directory"
        )

    # Check 2: Whitelist validation
    if config.whitelisted_dirs:
        in_whitelist = False
        for whitelisted in config.whitelisted_dirs:
            try:
                requested_path.relative_to(whitelisted.resolve())
                in_whitelist = True
                break
            except ValueError:
                continue
        
        if not in_whitelist:
            logger.warning(f"Path not in whitelist: '{user_path}'")
            raise PermissionError(
                f"Access denied: Path '{user_path}' not in allowed directories"
            )

    # Check 3: Blacklist patterns
    path_str = str(requested_path).lower()
    path_name = requested_path.name.lower()
    
    # Check critical patterns first
    for pattern in CRITICAL_PATTERNS:
        if pattern.lower() in path_str:
            logger.error(f"CRITICAL pattern detected: '{pattern}' in '{user_path}'")
            raise PermissionError(
                f"Access denied: Path contains forbidden pattern '{pattern}'"
            )
    
    # Check all forbidden patterns
    for category, patterns in FORBIDDEN_PATTERNS.items():
        for pattern in patterns:
            if pattern.lower() in path_str:
                if category == 'system_dirs' and path_str.startswith(pattern.lower()):
                    logger.warning(f"System directory access blocked: '{user_path}'")
                    raise PermissionError(
                        f"Access denied: System directory access not allowed"
                    )
                elif pattern.lower() in path_str:
                    logger.warning(f"Forbidden pattern detected: '{pattern}' in '{user_path}'")
                    raise PermissionError(
                        f"Access denied: Path contains forbidden pattern '{pattern}'"
                    )

    # Check 4: Self-modification protection (write access)
    if access_type in ('write', 'modify', 'delete') and config.protect_source:
        try:
            requested_path.relative_to(config.source_dir.resolve())
            logger.error(f"Self-modification attempt: '{user_path}' in source directory")
            raise PermissionError(
                f"Access denied: Cannot modify source code directory"
            )
        except ValueError:
            pass  # Not in source dir, OK

    # Check 5: Block writes to forbidden extensions
    if access_type in ('write', 'modify'):
        forbidden_extensions = {'.exe', '.dll', '.so', '.dylib', '.bin', '.sh'}
        if requested_path.suffix.lower() in forbidden_extensions:
            logger.warning(f"Forbidden file type write: '{requested_path.suffix}'")
            raise PermissionError(
                f"Access denied: Cannot write executable/binary files"
            )

    logger.debug(f"Path validated: '{user_path}' -> '{requested_path}' ({access_type})")
    return requested_path


def validate_path_whitelist_only(
    user_path: Union[str, Path],
    allowed_dirs: List[Union[str, Path]]
) -> Path:
    """
    Strict whitelist-only path validation.
    
    Only allows access to explicitly whitelisted directories.
    
    Args:
        user_path: Path to validate
        allowed_dirs: List of explicitly allowed directories
        
    Returns:
        Resolved Path if valid
        
    Raises:
        PermissionError: If path not in whitelist
    """
    if isinstance(user_path, str):
        requested_path = Path(user_path).expanduser().resolve()
    else:
        requested_path = user_path.expanduser().resolve()

    # Normalize allowed directories
    normalized_allowed = [
        Path(d).expanduser().resolve() if isinstance(d, str) else d.expanduser().resolve()
        for d in allowed_dirs
    ]

    # Check if path is within any allowed directory
    in_whitelist = False
    for allowed in normalized_allowed:
        try:
            requested_path.relative_to(allowed)
            in_whitelist = True
            break
        except ValueError:
            continue

    if not in_whitelist:
        allowed_str = ', '.join(str(a) for a in normalized_allowed)
        logger.warning(f"Path '{user_path}' not in whitelist: {allowed_str}")
        raise PermissionError(
            f"Access denied: Path must be within allowed directories: {allowed_str}"
        )

    return requested_path


def validate_file_size(file_path: Path, max_size: int = 10 * 1024 * 1024) -> None:
    """
    Validate file size to prevent DoS through large file reads.
    
    Args:
        file_path: Path to the file to check
        max_size: Maximum allowed file size in bytes (default: 10MB)
        
    Raises:
        ValueError: If file is larger than max_size
    """
    file_size = file_path.stat().st_size
    if file_size > max_size:
        raise ValueError(
            f"File too large ({file_size/1024/1024:.2f}MB > {max_size/1024/1024}MB): {file_path}"
        )
