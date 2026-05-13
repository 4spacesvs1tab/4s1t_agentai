"""
Session management system for the 4S1T Agent AI framework.

Handles user sessions, session persistence, and session security.
"""
import secrets
import hashlib
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import logging
import json

from database.connection import get_database_connection
from utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class Session:
    """User session data."""
    id: str
    user_id: str
    token: str
    created_at: datetime
    expires_at: datetime
    last_activity: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """Manages user sessions and session security."""
    
    def __init__(self, session_timeout_minutes: int = 30):
        """
        Initialize session manager.
        
        Args:
            session_timeout_minutes: Session timeout in minutes
        """
        self.db = get_database_connection()
        self.session_timeout = timedelta(minutes=session_timeout_minutes)
        self._initialize_database()
        logger.info(f"Session manager initialized with {session_timeout_minutes} minute timeout")
    
    def _initialize_database(self):
        """Initialize session-related database tables."""
        try:
            # Create sessions table
            create_sessions_table = """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_token TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_activity TEXT NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    metadata TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """
            self.db.execute_command(create_sessions_table)
            
            # Create session logs table
            create_logs_table = """
                CREATE TABLE IF NOT EXISTS session_logs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES user_sessions (id)
                )
            """
            self.db.execute_command(create_logs_table)
            
            logger.info("Session database tables initialized")
        except Exception as e:
            logger.error(f"Failed to initialize session database tables: {e}")
            raise
    
    def create_session(
        self, 
        user_id: str, 
        ip_address: Optional[str] = None, 
        user_agent: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Session:
        """
        Create a new user session.
        
        Args:
            user_id: User ID
            ip_address: Client IP address
            user_agent: Client user agent
            metadata: Additional session metadata
            
        Returns:
            Created session
        """
        try:
            session_id = self._generate_id()
            session_token = self._generate_secure_token()
            
            now = datetime.now()
            expires_at = now + self.session_timeout
            
            session = Session(
                id=session_id,
                user_id=user_id,
                token=session_token,
                created_at=now,
                expires_at=expires_at,
                last_activity=now,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata=metadata or {}
            )
            
            # Store in database
            metadata_str = json.dumps(session.metadata) if session.metadata else None
            insert_query = """
                INSERT INTO user_sessions 
                (id, user_id, session_token, created_at, expires_at, last_activity, ip_address, user_agent, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            self.db.execute_command(
                insert_query,
                (
                    session.id,
                    session.user_id,
                    session.token,
                    session.created_at.isoformat(),
                    session.expires_at.isoformat(),
                    session.last_activity.isoformat(),
                    session.ip_address,
                    session.user_agent,
                    metadata_str
                )
            )
            
            # Log session creation
            self._log_session_action(session.id, "CREATE", "Session created", ip_address, user_agent)
            
            logger.info(f"Session created for user {user_id}")
            return session
        except Exception as e:
            logger.error(f"Failed to create session for user {user_id}: {e}")
            raise
    
    def get_session(self, session_token: str) -> Optional[Session]:
        """
        Get a session by token.
        
        Args:
            session_token: Session token
            
        Returns:
            Session object or None if not found or expired
        """
        try:
            query = """
                SELECT * FROM user_sessions 
                WHERE session_token = ? AND is_active = 1 AND expires_at > datetime('now')
            """
            result = self.db.execute_query(query, (session_token,))
            
            if not result:
                return None
            
            row = result[0]
            
            # Parse metadata
            metadata = {}
            if row["metadata"]:
                try:
                    metadata = json.loads(row["metadata"])
                except json.JSONDecodeError:
                    logger.warning(f"Invalid metadata JSON for session {row['id']}")
            
            session = Session(
                id=row["id"],
                user_id=row["user_id"],
                token=row["session_token"],
                created_at=datetime.fromisoformat(row["created_at"]),
                expires_at=datetime.fromisoformat(row["expires_at"]),
                last_activity=datetime.fromisoformat(row["last_activity"]),
                ip_address=row["ip_address"],
                user_agent=row["user_agent"],
                metadata=metadata
            )
            
            return session
        except Exception as e:
            logger.error(f"Failed to get session: {e}")
            return None
    
    def refresh_session(self, session_token: str) -> Optional[Session]:
        """
        Refresh a session's expiration time.
        
        Args:
            session_token: Session token
            
        Returns:
            Refreshed session or None if not found
        """
        try:
            # Get current session
            session = self.get_session(session_token)
            if not session:
                return None
            
            # Update expiration time
            session.expires_at = datetime.now() + self.session_timeout
            session.last_activity = datetime.now()
            
            # Update in database
            update_query = """
                UPDATE user_sessions 
                SET expires_at = ?, last_activity = ? 
                WHERE session_token = ?
            """
            self.db.execute_command(
                update_query,
                (
                    session.expires_at.isoformat(),
                    session.last_activity.isoformat(),
                    session_token
                )
            )
            
            # Log session refresh
            self._log_session_action(session.id, "REFRESH", "Session refreshed", session.ip_address, session.user_agent)
            
            logger.info(f"Session refreshed for user {session.user_id}")
            return session
        except Exception as e:
            logger.error(f"Failed to refresh session: {e}")
            return None
    
    def invalidate_session(self, session_token: str) -> bool:
        """
        Invalidate a session.
        
        Args:
            session_token: Session token
            
        Returns:
            True if session invalidated successfully, False otherwise
        """
        try:
            update_query = "UPDATE user_sessions SET is_active = 0 WHERE session_token = ?"
            self.db.execute_command(update_query, (session_token,))
            
            logger.info(f"Session invalidated: {session_token[:10]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate session: {e}")
            return False
    
    def invalidate_user_sessions(self, user_id: str) -> int:
        """
        Invalidate all sessions for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Number of sessions invalidated
        """
        try:
            update_query = "UPDATE user_sessions SET is_active = 0 WHERE user_id = ? AND is_active = 1"
            cursor = self.db.execute_command(update_query, (user_id,))
            
            # Get number of affected rows
            # Note: SQLite's execute_command doesn't return row count directly
            # We'll query to get the count
            query = "SELECT COUNT(*) as count FROM user_sessions WHERE user_id = ? AND is_active = 0"
            result = self.db.execute_query(query, (user_id,))
            count = result[0]["count"] if result else 0
            
            logger.info(f"Invalidated {count} sessions for user {user_id}")
            return count
        except Exception as e:
            logger.error(f"Failed to invalidate user sessions for {user_id}: {e}")
            return 0
    
    def get_user_sessions(self, user_id: str, active_only: bool = True) -> List[Session]:
        """
        Get all sessions for a user.
        
        Args:
            user_id: User ID
            active_only: If True, only return active sessions
            
        Returns:
            List of sessions
        """
        try:
            if active_only:
                query = """
                    SELECT * FROM user_sessions 
                    WHERE user_id = ? AND is_active = 1 AND expires_at > datetime('now')
                    ORDER BY last_activity DESC
                """
            else:
                query = "SELECT * FROM user_sessions WHERE user_id = ? ORDER BY last_activity DESC"
            
            results = self.db.execute_query(query, (user_id,))
            sessions = []
            
            for row in results:
                # Parse metadata
                metadata = {}
                if row["metadata"]:
                    try:
                        metadata = json.loads(row["metadata"])
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid metadata JSON for session {row['id']}")
                
                session = Session(
                    id=row["id"],
                    user_id=row["user_id"],
                    token=row["session_token"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    expires_at=datetime.fromisoformat(row["expires_at"]),
                    last_activity=datetime.fromisoformat(row["last_activity"]),
                    ip_address=row["ip_address"],
                    user_agent=row["user_agent"],
                    metadata=metadata
                )
                sessions.append(session)
            
            return sessions
        except Exception as e:
            logger.error(f"Failed to get sessions for user {user_id}: {e}")
            return []
    
    def cleanup_expired_sessions(self) -> int:
        """
        Clean up expired sessions from the database.
        
        Returns:
            Number of sessions cleaned up
        """
        try:
            delete_query = "DELETE FROM user_sessions WHERE expires_at <= datetime('now')"
            cursor = self.db.execute_command(delete_query)
            
            # Get number of affected rows
            query = "SELECT COUNT(*) as count FROM user_sessions WHERE expires_at <= datetime('now')"
            result = self.db.execute_query(query)
            count = result[0]["count"] if result else 0
            
            if count > 0:
                logger.info(f"Cleaned up {count} expired sessions")
            
            return count
        except Exception as e:
            logger.error(f"Failed to clean up expired sessions: {e}")
            return 0
    
    def _log_session_action(
        self, 
        session_id: str, 
        action: str, 
        details: str, 
        ip_address: Optional[str] = None, 
        user_agent: Optional[str] = None
    ) -> None:
        """
        Log a session action.
        
        Args:
            session_id: Session ID
            action: Action type
            details: Action details
            ip_address: Client IP address
            user_agent: Client user agent
        """
        try:
            log_id = self._generate_id()
            insert_query = """
                INSERT INTO session_logs 
                (id, session_id, action, details, ip_address, user_agent, created_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """
            self.db.execute_command(
                insert_query,
                (log_id, session_id, action, details, ip_address, user_agent)
            )
        except Exception as e:
            logger.error(f"Failed to log session action: {e}")
    
    def get_session_logs(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get logs for a session.
        
        Args:
            session_id: Session ID
            limit: Maximum number of logs to return
            
        Returns:
            List of session logs
        """
        try:
            query = """
                SELECT * FROM session_logs 
                WHERE session_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            """
            results = self.db.execute_query(query, (session_id, limit))
            return results
        except Exception as e:
            logger.error(f"Failed to get session logs for {session_id}: {e}")
            return []
    
    def _generate_id(self) -> str:
        """
        Generate a unique ID.
        
        Returns:
            Unique ID string
        """
        return secrets.token_hex(16)
    
    def _generate_secure_token(self) -> str:
        """
        Generate a secure session token.
        
        Returns:
            Secure token string
        """
        return secrets.token_urlsafe(32)


# Global session manager instance
session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """
    Get singleton session manager instance.
    
    Returns:
        SessionManager instance
    """
    global session_manager
    if session_manager is None:
        session_manager = SessionManager()
    return session_manager
