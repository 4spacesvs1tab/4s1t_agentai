"""
Authentication service for 4S1T Agent AI system.
Handles user authentication and authorization.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
import logging
import re
import traceback

import sqlite3
from core.security import SecurityManager
from database.connection import get_database_connection
from services.mfa.service import get_mfa_service, MFAType
from services.rbac.permissions import get_rbac_service, Permission
from services.session.manager import get_session_manager
from services.exceptions import AuthError, AccountLockedError, DatabaseError, ValidationError
from utils.logger import setup_logger

logger = setup_logger(__name__)
security_manager = SecurityManager()
mfa_service = get_mfa_service()
rbac_service = get_rbac_service()
session_manager = get_session_manager()

# P3-3: Account lockout constants
_LOCKOUT_MAX_ATTEMPTS = 10
_LOCKOUT_DURATION_MINUTES = 15

# Cached dummy hash for constant-time comparison when username is not found.
# Computed lazily on first use so module import stays fast.
_dummy_hash_cache: Optional[str] = None


def _get_dummy_hash() -> str:
    """Return a stable argon2id hash used for constant-time rejection of unknown usernames."""
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = security_manager.hash_password("4s1t_dummy_constant_time_placeholder")
    return _dummy_hash_cache


class AuthService:
    """Service for handling user authentication and authorization."""
    
    def __init__(self):
        """Initialize authentication service."""
        self.db = get_database_connection()
        self.security = security_manager
        self.mfa = mfa_service
        self.rbac = rbac_service
        self.session = session_manager
        logger.info("Authentication service initialized")
    
    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """
        Authenticate a user with username and password.

        Constant-time design: always runs verify_and_rehash() even when the
        username is not found, to prevent username-enumeration via timing.

        P3-3: raises AccountLockedError when the account is locked.

        Args:
            username: User's username
            password: User's password

        Returns:
            User data dict (without password_hash) if successful.

        Raises:
            AccountLockedError: Account is temporarily locked.
            DatabaseError: Transient DB failure.
            AuthError: Unexpected service error.
        """
        try:
            # Fetch user (may be None for unknown usernames)
            query = "SELECT * FROM users WHERE username = ? AND is_active = 1"
            users = self.db.execute_query(query, (username,))
            user = users[0] if users else None

            # Always run hash verification to equalise timing regardless of user existence.
            stored_hash = user["password_hash"] if user else _get_dummy_hash()
            is_valid, new_hash = self.security.verify_and_rehash(password, stored_hash)

            if user is None:
                logger.warning(f"Authentication failed: User '{username}' not found or inactive")
                return None

            # P3-3: Check lockout AFTER hash work (timing already equalised above).
            locked_until_str = user.get("locked_until")
            if locked_until_str:
                try:
                    locked_until = datetime.fromisoformat(locked_until_str)
                    if locked_until.tzinfo is None:
                        locked_until = locked_until.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) < locked_until:
                        logger.warning(
                            f"Authentication blocked: account locked for '{username}' "
                            f"until {locked_until_str}"
                        )
                        raise AccountLockedError(locked_until=locked_until_str)
                except AccountLockedError:
                    raise
                except Exception as parse_err:
                    logger.error(f"Could not parse locked_until for '{username}': {parse_err}")

            if is_valid:
                # Persist migrated hash when upgrading from legacy SHA-256
                if new_hash:
                    rehash_query = "UPDATE users SET password_hash = ? WHERE id = ?"
                    self.db.execute_command(rehash_query, (new_hash, user["id"]))
                    logger.info(f"Password hash migrated to argon2id for user '{username}'")

                # Reset lockout counters and record last_login in one write
                try:
                    self.db.execute_command(
                        "UPDATE users SET failed_login_count = 0, locked_until = NULL, "
                        "last_login = datetime('now') WHERE id = ?",
                        (user["id"],),
                    )
                except (sqlite3.OperationalError, DatabaseError):
                    # Columns may not exist yet before migration 004 is applied; fall back.
                    # DatabaseError is raised by connection.py wrapping sqlite3.OperationalError.
                    self.db.execute_command(
                        "UPDATE users SET last_login = datetime('now') WHERE id = ?",
                        (user["id"],),
                    )

                logger.info(f"User '{username}' (id: {user['id']}) authenticated successfully")
                return {
                    "id": user["id"],
                    "username": user["username"],
                    "role": user["role"],
                    "is_active": user["is_active"],
                    "created_at": user["created_at"],
                    "last_login": user["last_login"] if user["last_login"] else None,
                }

            # Password incorrect — increment failure counter and possibly lock account.
            logger.warning(f"Authentication failed: Invalid password for user '{username}'")
            self._record_failed_attempt(user)
            return None

        except AccountLockedError:
            raise

        except sqlite3.OperationalError as e:
            logger.error(f"Database operational error during authentication: {e}")
            raise DatabaseError("Database service unavailable") from e

        except sqlite3.ProgrammingError as e:
            logger.error(f"SQL programming error during authentication: {e}")
            raise DatabaseError("Internal database error") from e

        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}", exc_info=True)
            raise AuthError("Authentication service error") from e

    def _record_failed_attempt(self, user: Dict[str, Any]) -> None:
        """Increment failed_login_count; lock account after _LOCKOUT_MAX_ATTEMPTS failures."""
        try:
            new_count = (user.get("failed_login_count") or 0) + 1
            if new_count >= _LOCKOUT_MAX_ATTEMPTS:
                locked_until = datetime.now(timezone.utc) + timedelta(minutes=_LOCKOUT_DURATION_MINUTES)
                locked_until_str = locked_until.strftime("%Y-%m-%dT%H:%M:%SZ")
                self.db.execute_command(
                    "UPDATE users SET failed_login_count = ?, locked_until = ? WHERE id = ?",
                    (new_count, locked_until_str, user["id"]),
                )
                logger.warning(
                    f"Account locked for user id={user['id']} until {locked_until_str} "
                    f"after {new_count} consecutive failures"
                )
            else:
                self.db.execute_command(
                    "UPDATE users SET failed_login_count = ? WHERE id = ?",
                    (new_count, user["id"]),
                )
                logger.info(
                    f"Failed login attempt {new_count}/{_LOCKOUT_MAX_ATTEMPTS} for user id={user['id']}"
                )
        except (sqlite3.OperationalError, DatabaseError) as e:
            # Columns don't exist yet (pre-migration); log and continue silently.
            # DatabaseError is raised by connection.py wrapping sqlite3.OperationalError.
            logger.warning(f"_record_failed_attempt: lockout columns not yet available: {e}")
    
    def authenticate_user_with_mfa(self, username: str, password: str, mfa_code: str = None, mfa_method: str = None) -> Optional[Dict[str, Any]]:
        """
        Authenticate a user with username, password and optional MFA.
        
        Args:
            username: User's username
            password: User's password
            mfa_code: MFA code (if required)
            mfa_method: MFA method type
            
        Returns:
            User data if authentication successful, None otherwise
        """
        # First authenticate with username and password
        user_data = self.authenticate_user(username, password)
        if not user_data:
            return None
        
        user_id = user_data["id"]
        
        # Check if MFA is enabled for this user
        if not self.mfa.is_mfa_enabled_for_user(user_id):
            # MFA not enabled, authentication complete
            return user_data
        
        # MFA is enabled, require MFA code
        if not mfa_code or not mfa_method:
            logger.warning(f"MFA required for user {user_id} but no code provided")
            # Return user data with MFA required flag
            user_data["mfa_required"] = True
            user_data["mfa_methods"] = self.mfa.get_user_mfa_methods(user_id)
            return user_data
        
        # Verify MFA code
        is_valid = False
        if mfa_method == MFAType.TOTP:
            is_valid = self.mfa.verify_totp_code(user_id, mfa_code)
        elif mfa_method == MFAType.BACKUP_CODES:
            is_valid, _ = self.mfa.verify_backup_code(user_id, mfa_code)
        else:
            logger.warning(f"Unsupported MFA method: {mfa_method}")
            return None
        
        if not is_valid:
            logger.warning(f"Invalid MFA code for user {user_id}")
            return None
        
        # MFA verification successful
        logger.info(f"MFA verification successful for user {user_id}")
        return user_data
    
    def create_access_token(self, user_data: dict, expires_delta: Optional[timedelta] = None, token_type: str = "access", extra_claims: Optional[Dict[str, Any]] = None) -> str:
        """
        Create an access token for an authenticated user.

        Args:
            user_data: User data to encode in the token
            expires_delta: Token expiration time (default: 30 minutes)
            token_type: Token type (access, mfa_enrollment, etc.)
            extra_claims: Additional claims to include in the token (e.g. mfa_verified)

        Returns:
            Access token
        """
        try:
            if expires_delta is None:
                expires_delta = timedelta(minutes=30)

            token_data = {
                "sub": user_data["id"],
                "user_id": user_data["id"],
                "token_type": token_type
            }

            if extra_claims:
                token_data.update(extra_claims)

            access_token = self.security.create_token(
                data=token_data,
                expires_delta=expires_delta
            )
            logger.info(f"Access token created for user {user_data['id']} (type: {token_type})")
            return access_token
        except Exception as e:
            logger.error(f"Failed to create access token: {e}")
            raise
    
    def _validate_username(self, username: str) -> bool:
        """
        Validate username format.
        
        Args:
            username: Username to validate
            
        Returns:
            True if username is valid, False otherwise
        """
        if not username or len(username) < 3 or len(username) > 50:
            logger.warning(f"Username validation failed: length must be 3-50 characters")
            return False
        
        # Only alphanumeric and underscores
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            logger.warning(f"Username validation failed: invalid characters in '{username}'")
            return False
        
        return True
    
    def _validate_password(self, password: str) -> bool:
        """
        Validate password strength.
        
        Args:
            password: Password to validate
            
        Returns:
            True if password meets requirements, False otherwise
        """
        if len(password) < 12:
            logger.warning("Password validation failed: must be at least 12 characters")
            return False
        
        # Check for uppercase, lowercase, numbers, and special characters
        has_upper = any(c.isupper() for c in password)
        has_lower = any(c.islower() for c in password)
        has_digit = any(c.isdigit() for c in password)
        has_special = any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password)
        
        if not (has_upper and has_lower and has_digit and has_special):
            logger.warning("Password validation failed: must contain uppercase, lowercase, digit, and special character")
            return False
        
        return True
    
    def create_user(self, username: str, password: str) -> bool:
        """
        Create a new user with username and password, with comprehensive error handling.
        MFA is mandatory for all users - creates both user and MFA records.
        
        Args:
            username: User's username (must be unique)
            password: User's password (will be hashed)
            
        Returns:
            True if user created successfully
        
        Raises:
            ValidationError: Username/password validation failure
            AuthError: Database integrity violation
        """
        try:
            # Validate inputs first
            if not self._validate_username(username):
                raise ValidationError(f"Invalid username format '{username}'")
            
            # Validate password strength
            if not self._validate_password(password):
                raise ValidationError("Password does not meet strength requirements")
            
            hashed_password = self.security.hash_password(password)
            
            # Insert user with proper error handling
            insert_query = """
                INSERT INTO users (id, username, password_hash, role, is_active, mfa_required, created_at)
                VALUES (hex(randomblob(16)), ?, ?, 'user', 1, 1, datetime('now'))
            """
            self.db.execute_command(insert_query, (username, hashed_password))
            
            # Get the created user ID
            user_query = "SELECT id FROM users WHERE username = ? ORDER BY created_at DESC LIMIT 1"
            users = self.db.execute_query(user_query, (username,))
            
            if not users:
                logger.error(f"User created but not found: {username}")
                raise AuthError("User creation failed - could not retrieve user ID")
            
            user_id = users[0]["id"]
            
            # Create MFA record (mandatory MFA for all users)
            mfa_query = """
                INSERT INTO user_mfa (id, user_id, mfa_enabled, enrollment_complete, 
                                    mfa_verified, created_at, updated_at)
                VALUES (hex(randomblob(16)), ?, 1, 0, 0, datetime('now'), datetime('now'))
            """
            self.db.execute_command(mfa_query, (user_id,))
            
            logger.info(f"User created with mandatory MFA: {username} (id: {user_id})")
            return True
            
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                # Username already exists - security best practice
                logger.info(f"Creation attempt: username '{username}' already exists")
                raise ValidationError(f"Username '{username}' is already taken")
            else:
                logger.error(f"Database integrity error: {e}")
                raise AuthError("Database integrity violation") from e
                
        except sqlite3.OperationalError as e:
            logger.error(f"Database operational error: {e}")
            raise DatabaseError("Database service unavailable") from e
            
        except sqlite3.ProgrammingError as e:
            logger.error(f"SQL programming error: {e}")
            raise DatabaseError("Internal database error") from e
            
        except ValidationError as e:
            # Input validation errors
            logger.warning(f"Validation failed: {e}")
            raise
            
        except Exception as e:
            # Unexpected errors
            logger.error(f"Unexpected error during user creation: {e}", exc_info=True)
            raise AuthError("Internal server error") from e
    
    def initialize_database(self):
        """Initialize the users table in the database."""
        try:
            # Create users table if it doesn't exist
            # FIXED: Removed email and full_name columns to match application requirements
            create_table_query = """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login TEXT
                )
            """
            self.db.execute_command(create_table_query)
            logger.info("Users table initialized")
        except Exception as e:
            logger.error(f"Failed to initialize users table: {e}")
            raise
    
    def check_user_permission(self, user_id: str, permission: Permission) -> bool:
        """
        Check if a user has a specific permission.
        
        Args:
            user_id: User ID
            permission: Permission to check
            
        Returns:
            True if user has permission, False otherwise
        """
        try:
            return self.rbac.check_permission(user_id, permission)
        except Exception as e:
            logger.error(f"Failed to check permission {permission.value} for user {user_id}: {e}")
            return False
    
    def check_user_permissions(self, user_id: str, permissions: List[Permission], require_all: bool = True) -> bool:
        """
        Check if a user has multiple permissions.
        
        Args:
            user_id: User ID
            permissions: List of permissions to check
            require_all: If True, user must have all permissions. If False, user must have at least one.
            
        Returns:
            True if permission check passes, False otherwise
        """
        try:
            return self.rbac.check_multiple_permissions(user_id, permissions, require_all)
        except Exception as e:
            logger.error(f"Failed to check multiple permissions for user {user_id}: {e}")
            return False
    
    def get_user_permissions(self, user_id: str) -> List[str]:
        """
        Get all permissions for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of permission strings
        """
        try:
            permissions = self.rbac.get_user_permissions(user_id)
            return [perm.value for perm in permissions]
        except Exception as e:
            logger.error(f"Failed to get permissions for user {user_id}: {e}")
            return []
    
    def create_user_session(
        self, 
        user_id: str, 
        ip_address: Optional[str] = None, 
        user_agent: Optional[str] = None
    ) -> Optional[str]:
        """
        Create a new user session.
        
        Args:
            user_id: User ID
            ip_address: Client IP address
            user_agent: Client user agent
            
        Returns:
            Session token or None if failed
        """
        try:
            session = self.session.create_session(user_id, ip_address, user_agent)
            return session.token
        except Exception as e:
            logger.error(f"Failed to create session for user {user_id}: {e}")
            return None
    
    def validate_user_session(self, session_token: str) -> Optional[Dict[str, Any]]:
        """
        Validate a user session.
        
        Args:
            session_token: Session token
            
        Returns:
            User data if session is valid, None otherwise
        """
        try:
            session = self.session.get_session(session_token)
            if not session:
                return None
            
            # Get user data
            query = "SELECT * FROM users WHERE id = ?"
            users = self.db.execute_query(query, (session.user_id,))
            
            if not users:
                return None
            
            user = users[0]
            
            # Refresh session
            self.session.refresh_session(session_token)
            
            return {
                "id": user["id"],
                "role": user["role"],
                "is_active": user["is_active"],
                "created_at": user["created_at"],
                "last_login": user["last_login"] if user["last_login"] else None,
                "session_id": session.id
            }
        except Exception as e:
            logger.error(f"Failed to validate session: {e}")
            return None
    
    def invalidate_user_session(self, session_token: str) -> bool:
        """
        Invalidate a user session.
        
        Args:
            session_token: Session token
            
        Returns:
            True if session invalidated successfully, False otherwise
        """
        try:
            return self.session.invalidate_session(session_token)
        except Exception as e:
            logger.error(f"Failed to invalidate session: {e}")
            return False
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get user by ID.

        Args:
            user_id: User ID

        Returns:
            User data if found, None otherwise
        """
        try:
            query = "SELECT * FROM users WHERE id = ?"
            users = self.db.execute_query(query, (user_id,))
            return users[0] if users else None
        except Exception as e:
            logger.error(f"Failed to get user by ID {user_id}: {e}")
            return None

    def get_all_users(self) -> List[Dict[str, Any]]:
        """
        Return all users with non-sensitive fields only.

        Admin-only; callers must verify the requesting user is an admin
        before invoking this method.

        Returns:
            List of user dicts ordered by creation date (newest first).
        """
        try:
            rows = self.db.execute_query(
                "SELECT id, username, role, created_at, is_active "
                "FROM users ORDER BY created_at DESC"
            )
            return rows or []
        except Exception as e:
            logger.error(f"Failed to retrieve all users: {e}")
            return []
    
    def generate_mfa_secret(self) -> str:
        """
        Generate a new MFA TOTP secret.
        
        Returns:
            Base32-encoded TOTP secret
        """
        try:
            return self.mfa.generate_totp_secret()
        except Exception as e:
            logger.error(f"Failed to generate MFA secret: {e}")
            raise AuthError("Failed to generate MFA secret") from e
    
    def generate_backup_codes(self) -> List[str]:
        """
        Generate a set of backup codes.
        
        Returns:
            List of backup codes
        """
        try:
            return self.mfa.generate_backup_codes()
        except Exception as e:
            logger.error(f"Failed to generate backup codes: {e}")
            raise AuthError("Failed to generate backup codes") from e
    
    def verify_mfa(self, user_id: str, token: str) -> bool:
        """
        Verify an MFA token for a user.
        
        Args:
            user_id: User ID
            token: MFA token to verify
            
        Returns:
            True if token is valid, False otherwise
        """
        try:
            return self.mfa.verify_totp_code(user_id, token)
        except Exception as e:
            logger.error(f"Failed to verify MFA token for user {user_id}: {e}")
            return False
    
    def _store_mfa_credentials(self, user_id: str, mfa_secret: str, backup_codes: List[str]) -> None:
        """
        Store MFA credentials for a user.
        
        Args:
            user_id: User ID
            mfa_secret: TOTP secret
            backup_codes: List of backup codes
        """
        try:
            # Store MFA secret in settings
            self.mfa.update_mfa_settings(
                user_id=user_id,
                totp_enabled=True,
                totp_secret=mfa_secret
            )
            
            # Store backup codes
            self.mfa._store_backup_codes(user_id, backup_codes)
            
            logger.info(f"MFA credentials stored for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to store MFA credentials for user {user_id}: {e}")
            raise AuthError("Failed to store MFA credentials") from e
    
    def _replace_backup_codes(self, user_id: str, new_codes: List[str]) -> None:
        """
        Replace existing backup codes with new ones.
        
        Args:
            user_id: User ID
            new_codes: New list of backup codes
        """
        try:
            # Delete old codes
            self.mfa._delete_backup_codes(user_id)
            
            # Store new codes
            self.mfa._store_backup_codes(user_id, new_codes)
            
            logger.info(f"Backup codes replaced for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to replace backup codes for user {user_id}: {e}")
            raise AuthError("Failed to replace backup codes") from e
    
    def update_user_theme(self, user_id: str, theme_preference: str) -> None:
        """
        Update user's theme preference in database.

        Args:
            user_id: User ID
            theme_preference: Theme preference string

        Raises:
            DatabaseError: If update fails
        """
        try:
            query = "UPDATE users SET theme_preference = ? WHERE id = ?"
            self.db.execute_command(query, (theme_preference, user_id))
            logger.info(f"Updated theme preference for user {user_id}: {theme_preference}")
        except Exception as e:
            logger.error(f"Failed to update theme preference for user {user_id}: {e}")
            raise DatabaseError("Failed to update theme preference") from e

    def update_user_language(self, user_id: str, language_preference: str) -> None:
        """
        Update user's language preference in database.

        Args:
            user_id: User ID
            language_preference: Language code (e.g. 'en', 'pl')

        Raises:
            DatabaseError: If update fails
        """
        try:
            query = "UPDATE users SET language_preference = ? WHERE id = ?"
            self.db.execute_command(query, (language_preference, user_id))
            logger.info(f"Updated language preference for user {user_id}: {language_preference}")
        except Exception as e:
            logger.error(f"Failed to update language preference for user {user_id}: {e}")
            raise DatabaseError("Failed to update language preference") from e

    def update_user_pii_scrubbing(self, user_id: str, enabled: bool) -> None:
        """
        Update user's PII scrubbing preference in database.

        Args:
            user_id: User ID
            enabled: True to enable PII scrubbing before LLM calls

        Raises:
            DatabaseError: If update fails
        """
        try:
            value = 1 if enabled else 0
            query = "UPDATE users SET pii_scrubbing_enabled = ? WHERE id = ?"
            self.db.execute_command(query, (value, user_id))
            logger.info(f"Updated PII scrubbing for user {user_id}: {enabled}")
        except Exception as e:
            logger.error(f"Failed to update PII scrubbing preference for user {user_id}: {e}")
            raise DatabaseError("Failed to update PII scrubbing preference") from e


# Global auth service instance
auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    """
    Get singleton authentication service instance.
    
    Returns:
        AuthService instance
    """
    global auth_service
    if auth_service is None:
        auth_service = AuthService()
    return auth_service
