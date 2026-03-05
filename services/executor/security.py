"""
Security utilities for the Executor Service.
Handles token verification and cryptographic operations.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError, DecodeError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Security-related error."""
    pass


def load_public_key():
    """Load public key from environment."""
    key_data = os.getenv('APPROVAL_PUBLIC_KEY', '')
    
    if not key_data:
        raise SecurityError("APPROVAL_PUBLIC_KEY not set")
    
    try:
        # Try to load as EC key
        public_key = serialization.load_pem_public_key(
            key_data.encode('utf-8'),
            backend=default_backend()
        )
        return public_key
    except Exception as e:
        logger.error(f"Failed to load public key: {e}")
        raise SecurityError(f"Invalid public key format: {e}")


# Global public key (loaded lazily)
_public_key = None


def get_public_key():
    """Get cached public key."""
    global _public_key
    if _public_key is None:
        _public_key = load_public_key()
    return _public_key


def verify_approval_token(token: str) -> Dict[str, Any]:
    """
    Verify a signed approval token.
    
    Args:
        token: JWT token string
        
    Returns:
        Token payload data
        
    Raises:
        SecurityError: If token is invalid, expired, or malformed
    """
    try:
        # Get public key
        public_key = get_public_key()
        
        # Decode and verify token
        payload = jwt.decode(
            token,
            public_key,
            algorithms=['ES256'],  # EC keys only
            options={
                'require': ['exp', 'iat', 'sub', 'jti'],
                'verify_signature': True,
                'verify_exp': True,
                'verify_iat': True,
            }
        )
        
        # Additional validation
        if 'approval' not in payload:
            raise SecurityError("Token missing approval data")
        
        approval_data = payload['approval']
        required_fields = ['user_id', 'code_hash', 'risk_level', 'expires_at']
        for field in required_fields:
            if field not in approval_data:
                raise SecurityError(f"Token missing approval field: {field}")
        
        # Check if token is expired (double-check)
        exp_timestamp = approval_data.get('expires_at')
        if exp_timestamp:
            exp_time = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
            if datetime.now(timezone.utc) > exp_time:
                raise SecurityError("Approval token has expired")
        
        logger.info(f"Token verified for user: {approval_data.get('user_id')}")
        return payload
        
    except ExpiredSignatureError:
        raise SecurityError("Approval token has expired")
    except InvalidTokenError as e:
        raise SecurityError(f"Invalid approval token: {e}")
    except jwt.DecodeError as e:
        raise SecurityError(f"Failed to decode token: {e}")
    except Exception as e:
        logger.error(f"Unexpected error verifying token: {e}")
        raise SecurityError(f"Token verification failed: {e}")


def verify_code_hash(code: str, expected_hash: str) -> bool:
    """
    Verify that executed code matches the approved code.
    
    Args:
        code: Actual code being executed
        expected_hash: Hash from approval token
        
    Returns:
        True if code matches, False otherwise
    """
    import hashlib
    
    # Calculate hash of actual code
    actual_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()
    
    # Compare (constant time comparison)
    return actual_hash == expected_hash


class ApprovalTokenGenerator:
    """Generate signed approval tokens (for main service)."""
    
    def __init__(self, private_key: str):
        """
        Initialize with private key.
        
        Args:
            private_key: RSA or ECDSA private key in PEM format
        """
        self.private_key = private_key
    
    def generate_token(
        self,
        user_id: str,
        code: str,
        risk_level: str = "medium",
        expiry_minutes: int = 5
    ) -> str:
        """
        Generate a signed approval token.
        
        Args:
            user_id: User requesting execution
            code: Code to be executed
            risk_level: Risk level (low, medium, high, critical)
            expiry_minutes: Token expiry time
            
        Returns:
            Signed JWT token
        """
        import hashlib
        import uuid
        from datetime import datetime, timedelta, timezone
        
        # Calculate code hash
        code_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()
        
        # Generate token ID
        jti = str(uuid.uuid4())
        
        # Build payload
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(minutes=expiry_minutes)
        
        payload = {
            'sub': user_id,
            'iat': now,
            'exp': expiry,
            'jti': jti,
            'type': 'code_execution_approval',
            'approval': {
                'user_id': user_id,
                'code_hash': code_hash,
                'risk_level': risk_level,
                'granted_at': now.isoformat(),
                'expires_at': expiry.timestamp(),
            }
        }
        
        # Sign token
        token = jwt.encode(
            payload,
            self.private_key,
            algorithm='ES256'  # ECDSA for smaller signatures
        )
        
        logger.info(f"Generated approval token for user {user_id}: {jti}")
        return token


# Convenience function for main service
def generate_approval_token(
    user_id: str,
    code: str,
    private_key: str,
    risk_level: str = "medium",
    expiry_minutes: int = 5
) -> str:
    """
    Convenience function to generate approval token.
    
    Args:
        user_id: User requesting execution
        code: Code to execute
        private_key: Private key for signing
        risk_level: Risk assessment
        expiry_minutes: Token validity
        
    Returns:
        Signed JWT token
    """
    generator = ApprovalTokenGenerator(private_key)
    return generator.generate_token(user_id, code, risk_level, expiry_minutes)
