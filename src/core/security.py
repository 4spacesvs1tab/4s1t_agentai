"""
Security framework for 4S1T Agent AI system.
Handles authentication, encryption, and data protection.
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple, Union
import logging
import hashlib
import hmac
import secrets
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
import jwt
from jwt.exceptions import PyJWTError

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Argon2id hasher — 6H.2: tuned for Core2Duo P8400 (Penryn) target.
# time_cost=2, memory_cost=32768 (32 MiB), parallelism=1
# Achieves ~0.5–1 s per hash on the deployment target while maintaining OWASP
# recommended argon2id security level for single-threaded hardware.
_ph = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=1)


def _is_legacy_sha256(hashed: str) -> bool:
    """Detect old 'salt$sha256hex' format (32-byte hex salt, 64-char hex digest)."""
    parts = hashed.split("$")
    return (
        len(parts) == 2
        and len(parts[0]) == 32
        and len(parts[1]) == 64
        and all(c in "0123456789abcdef" for c in parts[1])
    )


def _verify_legacy_sha256(plain_password: str, hashed_password: str) -> bool:
    """Constant-time verification of the legacy SHA-256 format."""
    try:
        salt, hash_part = hashed_password.split("$")
        expected = hashlib.sha256((salt + plain_password).encode()).hexdigest()
        return hmac.compare_digest(expected, hash_part)
    except Exception:
        return False


def verify_and_rehash(
    plain_password: str, hashed_password: str
) -> Tuple[bool, Optional[str]]:
    """
    Verify a password and transparently migrate legacy SHA-256 hashes to argon2id.

    Returns:
        (is_valid, new_hash) — new_hash is non-None only when the stored hash
        is a legacy SHA-256 entry that verified successfully and must be updated.
    """
    if _is_legacy_sha256(hashed_password):
        if _verify_legacy_sha256(plain_password, hashed_password):
            try:
                new_hash = _ph.hash(plain_password)
                logger.info("Migrated legacy SHA-256 password hash to argon2id")
                return True, new_hash
            except Exception as e:
                logger.error(f"Failed to re-hash password during migration: {e}")
                return True, None
        return False, None

    # Modern argon2 hash path
    try:
        _ph.verify(hashed_password, plain_password)
        if _ph.check_needs_rehash(hashed_password):
            new_hash = _ph.hash(plain_password)
            return True, new_hash
        return True, None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    except Exception as e:
        logger.error(f"Password verification failed: {e}")
        return False, None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a stored hash.

    Supports both legacy SHA-256 ('salt$hex') and modern argon2id formats.
    For migration-aware callers use verify_and_rehash() instead.

    Returns:
        True if the password matches, False otherwise.
    """
    is_valid, _ = verify_and_rehash(plain_password, hashed_password)
    return is_valid


def get_password_hash(password: str) -> str:
    """
    Hash a plain password using argon2id.

    Returns:
        Argon2id hash string (format managed by the argon2-cffi library).
    """
    try:
        return _ph.hash(password)
    except Exception as e:
        logger.error(f"Password hashing failed: {e}")
        raise


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.
    
    Args:
        data: Data to encode in the token
        expires_delta: Token expiration time
        
    Returns:
        Encoded JWT token
    """
    try:
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            
        to_encode.update({"exp": expire, "jti": str(uuid.uuid4())})
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        return encoded_jwt
    except Exception as e:
        logger.error(f"Token creation failed: {e}")
        raise


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decode a JWT access token.
    
    Args:
        token: JWT token to decode
        
    Returns:
        Decoded token data or None if invalid
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except PyJWTError as e:
        logger.error(f"Token decoding failed: {e}")
        return None


class SecurityManager:
    """Manages security operations for the 4S1T Agent AI system."""
    
    def __init__(self):
        """Initialize security manager."""
        logger.info("Security manager initialized")
    
    def hash_password(self, password: str) -> str:
        """
        Hash a password.
        
        Args:
            password: Plain text password
            
        Returns:
            Hashed password
        """
        return get_password_hash(password)
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """
        Verify a password.

        Args:
            plain_password: Plain text password
            hashed_password: Hashed password

        Returns:
            True if passwords match, False otherwise
        """
        return verify_password(plain_password, hashed_password)

    def verify_and_rehash(
        self, plain_password: str, hashed_password: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify a password with transparent hash migration support.

        Returns:
            (is_valid, new_hash) — new_hash is non-None when the stored hash
            should be updated (legacy SHA-256 migrated to argon2id).
        """
        return verify_and_rehash(plain_password, hashed_password)
    
    def create_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create an access token.
        
        Args:
            data: Data to encode in the token
            expires_delta: Token expiration time
            
        Returns:
            Encoded JWT token
        """
        return create_access_token(data, expires_delta)
    
    def decode_token(self, token: str) -> Optional[dict]:
        """
        Decode an access token.
        
        Args:
            token: JWT token to decode
            
        Returns:
            Decoded token data or None if invalid
        """
        return decode_access_token(token)
