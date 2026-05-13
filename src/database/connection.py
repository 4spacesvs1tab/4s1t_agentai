import os
import sqlite3
import stat
from typing import Optional, Generator, List, Dict, Any
import threading
from contextlib import contextmanager
import time
from pathlib import Path
import weakref

from config.settings import settings
from services.exceptions import DatabaseError

from utils.logger import setup_logger
logger = setup_logger(__name__)


class DatabaseConnection:
    """Enhanced database connection with security, performance, and connection pooling features.
    
    Priority 2.3 Implementation: Comprehensive connection pool with timeout configuration,
    max connections limit, automatic cleanup, and health validation.
    """
    
    def __init__(self, db_url: Optional[str] = None, timeout: float = 10.0, max_connections: int = 10):
        self.db_url = db_url or settings.DATABASE_URL
        self.timeout = timeout
        self.max_connections = max_connections
        self._closed = False
        
        # Thread safety primitives
        self._connection_lock = threading.RLock()
        self._pool_lock = threading.Lock()
        
        # Connection tracking and limiting
        self._active_connections = set()  # Regular set since SQLite connections can't be weakly referenced
        self._connection_semaphore = threading.Semaphore(max_connections)
        
        logger.info(f"Enhanced database connection initialized: {self.db_url}, timeout={timeout}, max_connections={max_connections}")
    
    def __del__(self):
        """Destructor to ensure cleanup on garbage collection."""
        try:
            self.close_all_connections()
        except Exception as e:
            logger.warning(f"Error during destructor cleanup: {e}")
    
    def _create_connection(self) -> sqlite3.Connection:
        """Create secure database connection with enhanced configuration."""
        try:
            if not self.db_url.startswith("sqlite:///"):
                raise DatabaseError("Database configuration error - invalid URL format")
            
            db_path = self.db_url.replace("sqlite:///", "")
            
            # Ensure directory exists and enforce 0o600 permissions on the DB file
            if ":memory:" not in db_path.lower():
                db_file = Path(db_path)
                if not db_file.parent.exists():
                    db_file.parent.mkdir(parents=True, exist_ok=True)
                # Create the file if it doesn't exist, then enforce 0o600
                if not db_file.exists():
                    db_file.touch()
                os.chmod(db_path, 0o600)

            # Create connection with enhanced settings including timeout
            connection = sqlite3.connect(
                db_path,
                check_same_thread=False,
                timeout=self.timeout  # Connection lock timeout
            )
            connection.row_factory = sqlite3.Row
            
            # Security and performance configurations
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL") 
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA secure_delete=ON")
            cursor.execute("PRAGMA temp_store=memory")
            cursor.close()
            
            return connection
            
        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}")
            raise DatabaseError("Database service unavailable") from e
        except OSError as e:
            logger.error(f"File system error: {e}")
            raise DatabaseError("Database file access error") from e
        except Exception as e:
            logger.error(f"Unexpected database error: {e}")
            raise DatabaseError("Database initialization failed") from e
    
    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get database connection with comprehensive error handling, cleanup, and connection limiting."""
        if self._closed:
            raise DatabaseError("Connection manager is closed")
        
        # Enforce connection limit with semaphore
        if not self._connection_semaphore.acquire(timeout=self.timeout):
            raise DatabaseError(f"Could not acquire database connection within {self.timeout} seconds")
        
        connection = None
        try:
            with self._connection_lock:  # Thread-safe connection creation
                connection = self._create_connection()
                self._active_connections.add(connection)
                logger.debug(f"Created new database connection (total: {len(self._active_connections)})")
            
            yield connection
            
        except DatabaseError as e:
            # Already properly categorized
            raise
        except sqlite3.IntegrityError as e:
            # Let IntegrityError propagate up for specific handling in service layer
            raise
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                logger.warning("Database locked - temporary unavailability")
                raise DatabaseError("Database temporarily unavailable") from e
            else:
                logger.error(f"Database operational error: {e}")
                raise DatabaseError("Database operational error") from e
        except sqlite3.ProgrammingError as e:
            logger.error(f"SQL programming error: {e}")
            raise DatabaseError("Database programming error") from e
        except Exception as e:
            logger.error(f"Unexpected database error: {e}")
            raise DatabaseError("Unexpected database error") from e
        finally:
            # Always release semaphore
            self._connection_semaphore.release()
            
            # Clean up connection if still open
            if connection:
                try:
                    self._active_connections.discard(connection)
                    connection.close()
                    logger.debug(f"Closed database connection (remaining: {len(self._active_connections)})")
                except Exception as e:
                    logger.warning(f"Error closing connection: {e}")
    
    def execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute SELECT query with error handling."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def execute_command(self, command: str, params: tuple = ()) -> int:
        """Execute INSERT/UPDATE/DELETE with error handling."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(command, params)
                conn.commit()
                return cursor.rowcount
            except sqlite3.IntegrityError as e:
                # Let IntegrityError propagate up for specific handling
                raise

    def initialize_database(self):
        """Initialize database with proper schema including MFA tables."""
        try:
            # Users table - FIXED: Removed email and full_name fields, added mfa_required
            users_query = """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    mfa_required BOOLEAN NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login TEXT
                )
            """
            self.execute_command(users_query)
            
            # MFA user configuration table
            mfa_users_query = """
                CREATE TABLE IF NOT EXISTS user_mfa (
                    id TEXT PRIMARY KEY DEFAULT (hex(randomblob(16))),
                    user_id TEXT UNIQUE NOT NULL,
                    authy_id TEXT,
                    phone_number TEXT,
                    country_code TEXT DEFAULT '+1',
                    mfa_enabled BOOLEAN NOT NULL DEFAULT 1,
                    enrollment_complete BOOLEAN NOT NULL DEFAULT 0,
                    mfa_verified BOOLEAN NOT NULL DEFAULT 0,
                    backup_codes TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_verified_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """
            self.execute_command(mfa_users_query)
            
            # MFA verification sessions table
            mfa_sessions_query = """
                CREATE TABLE IF NOT EXISTS mfa_sessions (
                    id TEXT PRIMARY KEY DEFAULT (hex(randomblob(16))),
                    user_id TEXT NOT NULL,
                    session_token TEXT UNIQUE NOT NULL,
                    auth_method TEXT NOT NULL DEFAULT 'totp',
                    expires_at TEXT NOT NULL,
                    verified BOOLEAN NOT NULL DEFAULT 0,
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """
            self.execute_command(mfa_sessions_query)
            
            # MFA audit log table
            mfa_audit_query = """
                CREATE TABLE IF NOT EXISTS mfa_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    method TEXT,
                    success BOOLEAN NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """
            self.execute_command(mfa_audit_query)
            
            logger.info("Security-enhanced database initialized with MFA support")
            logger.debug("Created tables: users, user_mfa, mfa_sessions, mfa_audit_log")
        except DatabaseError as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def close_all_connections(self):
        """Close all active connections safely."""
        self._closed = True
        with self._pool_lock:
            active_count = len(self._active_connections)
            for conn in list(self._active_connections):
                try:
                    conn.close()
                    self._active_connections.discard(conn)
                except Exception as e:
                    logger.warning(f"Error closing connection during shutdown: {e}")
            
            # Reset semaphore to allow new connections after close
            for _ in range(self.max_connections):
                try:
                    self._connection_semaphore.release()
                except ValueError:
                    break  # Semaphore already at max value
        
        logger.info(f"All database connections closed (was {active_count} active connections)")

    @classmethod
    def startup_permission_check(cls, db_url: Optional[str] = None) -> None:
        """
        Verify that the database file has 0o600 permissions.

        Raises:
            RuntimeError: If the file exists but has unsafe permissions.
        """
        from config.settings import settings as _settings
        url = db_url or _settings.DATABASE_URL
        if not url.startswith("sqlite:///"):
            return
        db_path = url.replace("sqlite:///", "")
        if ":memory:" in db_path.lower():
            return
        db_file = Path(db_path)
        if not db_file.exists():
            return
        mode = db_file.stat().st_mode & 0o777
        if mode != 0o600:
            raise RuntimeError(
                f"Database file '{db_path}' has unsafe permissions {oct(mode)}; "
                "expected 0o600. Run: chmod 600 " + db_path
            )
        logger.info(f"Database file permission check passed: {db_path} is 0o600")

    def health_check(self) -> dict:
        """Enhanced health check with connection pool status."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
            
            active_count = len(self._active_connections)
            semaphore_value = self._connection_semaphore._value if hasattr(self._connection_semaphore, '_value') else 'unknown'
            
            return {
                "status": "healthy",
                "database": "ready",
                "connection_pool": {
                    "active_connections": active_count,
                    "max_connections": self.max_connections,
                    "available_slots": semaphore_value,
                    "timeout_seconds": self.timeout,
                    "is_closed": self._closed
                }
            }
        except DatabaseError as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "connection_pool": {
                    "active_connections": len(self._active_connections),
                    "max_connections": self.max_connections,
                    "is_closed": self._closed
                }
            }


# Global database connection instance
_database_connection: Optional[DatabaseConnection] = None


def get_database_connection() -> DatabaseConnection:
    """
    Get singleton database connection instance.
    
    Returns:
        DatabaseConnection instance
    """
    global _database_connection
    if _database_connection is None:
        _database_connection = DatabaseConnection()
    return _database_connection
