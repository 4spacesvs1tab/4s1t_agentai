"""
Multi-Factor Authentication (MFA) service for the 4S1T Agent AI framework.

Supports TOTP (Time-based One-Time Password) and backup codes for multi-factor authentication.
"""
import pyotp
import secrets
import hashlib
import base64
import qrcode
import io
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
import logging

from database.connection import get_database_connection
from utils.logger import setup_logger

logger = setup_logger(__name__)


class MFAType:
    """MFA type constants."""
    TOTP = "totp"
    BACKUP_CODES = "backup_codes"
    SMS = "sms"
    EMAIL = "email"


class MFAService:
    """Service for handling multi-factor authentication."""
    
    def __init__(self):
        """Initialize MFA service."""
        self.db = get_database_connection()
        self._initialize_database()
        logger.info("MFA service initialized")
    
    def _initialize_database(self):
        """Initialize MFA-related database tables."""
        try:
            # Create MFA methods table
            create_mfa_table = """
                CREATE TABLE IF NOT EXISTS mfa_methods (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    method_type TEXT NOT NULL,
                    secret TEXT,
                    backup_codes TEXT,
                    phone_number TEXT,
                    is_enabled BOOLEAN NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """
            self.db.execute_command(create_mfa_table)
            
            # Create MFA sessions table
            create_sessions_table = """
                CREATE TABLE IF NOT EXISTS mfa_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_token TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """
            self.db.execute_command(create_sessions_table)
            
            logger.info("MFA database tables initialized")
        except Exception as e:
            logger.error(f"Failed to initialize MFA database tables: {e}")
            raise
    
    def generate_totp_secret(self) -> str:
        """
        Generate a new TOTP secret.
        
        Returns:
            Base32-encoded secret string
        """
        return pyotp.random_base32()
    
    def generate_backup_codes(self, count: int = 10) -> List[str]:
        """
        Generate backup codes for MFA.
        
        Args:
            count: Number of backup codes to generate
            
        Returns:
            List of backup codes
        """
        codes = []
        for _ in range(count):
            # Generate a secure random code
            code = secrets.token_urlsafe(16)[:8].upper()
            codes.append(code)
        return codes
    
    def enable_totp_for_user(self, user_id: str, secret: str) -> bool:
        """
        Enable TOTP MFA for a user.
        
        Args:
            user_id: User ID
            secret: TOTP secret
            
        Returns:
            True if enabled successfully, False otherwise
        """
        try:
            # Check if user already has TOTP enabled
            query = "SELECT id FROM mfa_methods WHERE user_id = ? AND method_type = ? AND is_enabled = 1"
            existing = self.db.execute_query(query, (user_id, MFAType.TOTP))
            
            if existing:
                logger.warning(f"TOTP already enabled for user {user_id}")
                return False
            
            # Insert or update TOTP method
            mfa_id = secrets.token_hex(16)
            insert_query = """
                INSERT OR REPLACE INTO mfa_methods 
                (id, user_id, method_type, secret, is_enabled, created_at)
                VALUES (?, ?, ?, ?, 1, datetime('now'))
            """
            self.db.execute_command(insert_query, (mfa_id, user_id, MFAType.TOTP, secret))
            
            logger.info(f"TOTP enabled for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to enable TOTP for user {user_id}: {e}")
            return False
    
    def disable_totp_for_user(self, user_id: str) -> bool:
        """
        Disable TOTP MFA for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            True if disabled successfully, False otherwise
        """
        try:
            update_query = """
                UPDATE mfa_methods 
                SET is_enabled = 0, secret = NULL 
                WHERE user_id = ? AND method_type = ?
            """
            self.db.execute_command(update_query, (user_id, MFAType.TOTP))
            
            logger.info(f"TOTP disabled for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to disable TOTP for user {user_id}: {e}")
            return False
    
    def generate_totp_qr_code(self, secret: str, username: str, issuer: str = "4S1T Agent AI") -> bytes:
        """
        Generate a QR code for TOTP setup.
        
        Args:
            secret: TOTP secret
            username: Username
            issuer: Issuer name for the QR code
            
        Returns:
            QR code image bytes
        """
        try:
            # Create TOTP provisioning URI
            totp = pyotp.totp.TOTP(secret)
            provisioning_uri = totp.provisioning_uri(
                name=username,
                issuer_name=issuer
            )
            
            # Generate QR code
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(provisioning_uri)
            qr.make(fit=True)
            
            # Create image
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Convert to bytes
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            img_bytes.seek(0)
            
            return img_bytes.getvalue()
        except Exception as e:
            logger.error(f"Failed to generate QR code: {e}")
            raise
    
    def verify_totp_code(self, user_id: str, code: str) -> bool:
        """
        Verify a TOTP code for a user.
        
        Args:
            user_id: User ID
            code: TOTP code to verify
            
        Returns:
            True if code is valid, False otherwise
        """
        try:
            # Get user's TOTP secret
            query = "SELECT secret FROM mfa_methods WHERE user_id = ? AND method_type = ? AND is_enabled = 1"
            result = self.db.execute_query(query, (user_id, MFAType.TOTP))
            
            if not result:
                logger.warning(f"No TOTP method found for user {user_id}")
                return False
            
            secret = result[0]["secret"]
            if not secret:
                logger.warning(f"No TOTP secret found for user {user_id}")
                return False
            
            # Verify the code
            totp = pyotp.totp.TOTP(secret)
            is_valid = totp.verify(code, valid_window=1)
            
            if is_valid:
                # Update last used timestamp
                update_query = "UPDATE mfa_methods SET last_used_at = datetime('now') WHERE user_id = ? AND method_type = ?"
                self.db.execute_command(update_query, (user_id, MFAType.TOTP))
                logger.info(f"TOTP code verified for user {user_id}")
            else:
                logger.warning(f"Invalid TOTP code for user {user_id}")
            
            return is_valid
        except Exception as e:
            logger.error(f"Failed to verify TOTP code for user {user_id}: {e}")
            return False
    
    def enable_backup_codes_for_user(self, user_id: str, codes: List[str]) -> bool:
        """
        Enable backup codes for a user.
        
        Args:
            user_id: User ID
            codes: List of backup codes
            
        Returns:
            True if enabled successfully, False otherwise
        """
        try:
            # Hash the backup codes for storage
            hashed_codes = [self._hash_backup_code(code) for code in codes]
            codes_str = ",".join(hashed_codes)
            
            # Check if user already has backup codes enabled
            query = "SELECT id FROM mfa_methods WHERE user_id = ? AND method_type = ? AND is_enabled = 1"
            existing = self.db.execute_query(query, (user_id, MFAType.BACKUP_CODES))
            
            if existing:
                # Update existing backup codes
                update_query = """
                    UPDATE mfa_methods 
                    SET backup_codes = ?, last_used_at = NULL 
                    WHERE user_id = ? AND method_type = ?
                """
                self.db.execute_command(update_query, (codes_str, user_id, MFAType.BACKUP_CODES))
            else:
                # Insert new backup codes
                mfa_id = secrets.token_hex(16)
                insert_query = """
                    INSERT INTO mfa_methods 
                    (id, user_id, method_type, backup_codes, is_enabled, created_at)
                    VALUES (?, ?, ?, ?, 1, datetime('now'))
                """
                self.db.execute_command(insert_query, (mfa_id, user_id, MFAType.BACKUP_CODES, codes_str))
            
            logger.info(f"Backup codes enabled for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to enable backup codes for user {user_id}: {e}")
            return False
    
    def verify_backup_code(self, user_id: str, code: str) -> Tuple[bool, bool]:
        """
        Verify a backup code for a user.
        
        Args:
            user_id: User ID
            code: Backup code to verify
            
        Returns:
            Tuple of (is_valid, is_consumed) where:
            - is_valid: True if code is valid
            - is_consumed: True if code was already used/consumed
        """
        try:
            # Get user's backup codes
            query = "SELECT backup_codes FROM mfa_methods WHERE user_id = ? AND method_type = ? AND is_enabled = 1"
            result = self.db.execute_query(query, (user_id, MFAType.BACKUP_CODES))
            
            if not result:
                logger.warning(f"No backup codes found for user {user_id}")
                return False, False
            
            codes_str = result[0]["backup_codes"]
            if not codes_str:
                logger.warning(f"No backup codes stored for user {user_id}")
                return False, False
            
            hashed_codes = codes_str.split(",")
            hashed_input = self._hash_backup_code(code)
            
            # Check if code exists and is not consumed
            if hashed_input in hashed_codes:
                # Remove the used code (consume it)
                hashed_codes.remove(hashed_input)
                updated_codes_str = ",".join(hashed_codes)
                
                # Update the stored codes
                update_query = """
                    UPDATE mfa_methods 
                    SET backup_codes = ?, last_used_at = datetime('now') 
                    WHERE user_id = ? AND method_type = ?
                """
                self.db.execute_command(update_query, (updated_codes_str, user_id, MFAType.BACKUP_CODES))
                
                logger.info(f"Backup code verified and consumed for user {user_id}")
                return True, False
            else:
                # Check if it's an invalid code or already consumed
                logger.warning(f"Invalid or consumed backup code for user {user_id}")
                return False, hashed_input in [self._hash_backup_code(c) for c in codes_str.split(",")]
        except Exception as e:
            logger.error(f"Failed to verify backup code for user {user_id}: {e}")
            return False, False
    
    def get_user_mfa_methods(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all MFA methods for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of MFA methods
        """
        try:
            query = "SELECT id, method_type, is_enabled, created_at, last_used_at FROM mfa_methods WHERE user_id = ?"
            results = self.db.execute_query(query, (user_id,))
            
            methods = []
            for row in results:
                methods.append({
                    "id": row["id"],
                    "method_type": row["method_type"],
                    "is_enabled": bool(row["is_enabled"]),
                    "created_at": row["created_at"],
                    "last_used_at": row["last_used_at"]
                })
            
            return methods
        except Exception as e:
            logger.error(f"Failed to get MFA methods for user {user_id}: {e}")
            return []
    
    def is_mfa_enabled_for_user(self, user_id: str) -> bool:
        """
        Check if any MFA method is enabled for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            True if MFA is enabled, False otherwise
        """
        try:
            query = "SELECT COUNT(*) as count FROM mfa_methods WHERE user_id = ? AND is_enabled = 1"
            result = self.db.execute_query(query, (user_id,))
            return result[0]["count"] > 0
        except Exception as e:
            logger.error(f"Failed to check MFA status for user {user_id}: {e}")
            return False
    
    def create_verification_session(self, user_id: str, duration_minutes: int = 5) -> str:
        """
        Create an MFA verification session token for a user during login.
        
        Args:
            user_id: User ID
            duration_minutes: Session duration in minutes (default 5 for login verification)
            
        Returns:
            Session token
        """
        try:
            session_token = secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(minutes=duration_minutes)
            
            session_id = secrets.token_hex(16)
            insert_query = """
                INSERT INTO mfa_sessions 
                (id, user_id, session_token, expires_at, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
            """
            self.db.execute_command(
                insert_query, 
                (session_id, user_id, session_token, expires_at.isoformat())
            )
            
            logger.info(f"MFA verification session created for user {user_id}")
            return session_token
        except Exception as e:
            logger.error(f"Failed to create MFA verification session for user {user_id}: {e}")
            raise
    
    def create_mfa_session(self, user_id: str, duration_minutes: int = 30) -> str:
        """
        Create an MFA session token for a user (legacy/compatibility method).
        
        Args:
            user_id: User ID
            duration_minutes: Session duration in minutes
            
        Returns:
            Session token
        """
        return self.create_verification_session(user_id, duration_minutes)
    
    def verify_session(self, session_token: str, verification_code: str) -> Dict[str, Any]:
        """
        Verify a session token and MFA code.
        
        Args:
            session_token: Session token from login
            verification_code: TOTP code or backup code
            
        Returns:
            Dict with success status and user_id if successful
        """
        try:
            # Look up the session
            query = """
                SELECT user_id, expires_at FROM mfa_sessions 
                WHERE session_token = ? AND expires_at > datetime('now')
            """
            result = self.db.execute_query(query, (session_token,))
            
            if not result:
                logger.warning(f"Invalid or expired MFA session token")
                return {"success": False, "error": "Invalid or expired session"}
            
            user_id = result[0]["user_id"]
            
            # Try TOTP verification first
            if self.verify_totp_code(user_id, verification_code):
                # Invalidate the session after successful use
                self.db.execute_command(
                    "DELETE FROM mfa_sessions WHERE session_token = ?",
                    (session_token,)
                )
                logger.info(f"MFA session verified with TOTP for user {user_id}")
                return {"success": True, "user_id": user_id}
            
            # Try backup code verification
            is_valid, _ = self.verify_backup_code(user_id, verification_code)
            if is_valid:
                # Invalidate the session after successful use
                self.db.execute_command(
                    "DELETE FROM mfa_sessions WHERE session_token = ?",
                    (session_token,)
                )
                logger.info(f"MFA session verified with backup code for user {user_id}")
                return {"success": True, "user_id": user_id}
            
            logger.warning(f"MFA verification failed for user {user_id}")
            return {"success": False, "error": "Invalid verification code"}
            
        except Exception as e:
            logger.error(f"Failed to verify MFA session: {e}")
            return {"success": False, "error": "Verification failed"}
    
    def validate_mfa_session(self, user_id: str, session_token: str) -> bool:
        """
        Validate an MFA session token.
        
        Args:
            user_id: User ID
            session_token: Session token to validate
            
        Returns:
            True if session is valid, False otherwise
        """
        try:
            query = """
                SELECT expires_at FROM mfa_sessions 
                WHERE user_id = ? AND session_token = ? AND expires_at > datetime('now')
            """
            result = self.db.execute_query(query, (user_id, session_token))
            
            is_valid = len(result) > 0
            if is_valid:
                logger.info(f"MFA session validated for user {user_id}")
            else:
                logger.warning(f"Invalid or expired MFA session for user {user_id}")
            
            return is_valid
        except Exception as e:
            logger.error(f"Failed to validate MFA session for user {user_id}: {e}")
            return False
    
    def invalidate_mfa_session(self, user_id: str, session_token: str) -> bool:
        """
        Invalidate an MFA session token.
        
        Args:
            user_id: User ID
            session_token: Session token to invalidate
            
        Returns:
            True if invalidated successfully, False otherwise
        """
        try:
            delete_query = "DELETE FROM mfa_sessions WHERE user_id = ? AND session_token = ?"
            self.db.execute_command(delete_query, (user_id, session_token))
            
            logger.info(f"MFA session invalidated for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate MFA session for user {user_id}: {e}")
            return False
    
    def _hash_backup_code(self, code: str) -> str:
        """
        Hash a backup code for secure storage.
        
        Args:
            code: Backup code to hash
            
        Returns:
            Hashed code
        """
        return hashlib.sha256(code.encode()).hexdigest()



    def get_user_mfa_status(self, user_id: str) -> Dict[str, Any]:
        """Get complete MFA status for a user.
        
        MFA is MANDATORY for all users. This method checks if the user
        has completed MFA enrollment. If not, they must enroll before
        accessing the system.
        """
        # Check if user has TOTP MFA configured
        query = """
            SELECT method_type, secret, is_enabled, created_at
            FROM mfa_methods
            WHERE user_id = ?
        """
        results = self.db.execute_query(query, (user_id,))
        
        # Check if user has TOTP enrolled and enabled
        mfa_enabled = False
        enrollment_complete = False
        
        if results:
            for row in results:
                if row.get('method_type') == 'totp':
                    mfa_enabled = True
                    enrollment_complete = bool(row.get('is_enabled', False))
                    break
        
        # MFA is MANDATORY for all users - always required
        mfa_required = True
        
        return {
            "mfa_required": mfa_required,
            "mfa_enabled": mfa_enabled,
            "enrollment_complete": enrollment_complete,
            "verified": False,  # Would need session tracking
            "phone_number": None,  # Not in current schema
            "last_verified": None  # Not in current schema
        }

# Global MFA service instance
mfa_service: Optional[MFAService] = None


def get_mfa_service() -> MFAService:
    """
    Get singleton MFA service instance.
    
    Returns:
        MFAService instance
    """
    global mfa_service
    if mfa_service is None:
        mfa_service = MFAService()
    return mfa_service
