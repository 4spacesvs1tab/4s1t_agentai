import sqlite3
from typing import Optional, Generator, List, Dict, Any
import logging
import threading
from contextlib import contextmanager
import time
from pathlib import Path

from config.settings import settings
from services.exceptions import DatabaseError

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Enhanced database connection with security and performance features."""
    
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.DATABASE_URL
        self._closed = False
        self._lock = threading.RLock()
        logger.info(f"Enhanced database connection initialized: {self.db_url}")
    
    def _create_connection(self) -> sqlite3.Connection:
        """Create secure database connection with enhanced configuration."""
        try:
            if not self.db_url.startswith("sqlite:///"):
                raise DatabaseError("Database configuration error - invalid URL format")
            
            db_path = self.db_url.replace("sqlite:///", "")
            
            # Ensure directory exists
            if ":memory:" not in db_path.lower():
                db_file = Path(db_path)
                if not db_file.parent.exists():
                    db_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Create connection with enhanced settings
            connection = sqlite3.connect(db_path, check_same_thread=False)
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
        """Get database connection with comprehensive error handling and cleanup."""
        if self._closed:
            raise DatabaseError("Connection manager is closed")
        
        connection = None
        try:
            with self._lock:  # Thread-safe connection creation
                connection = self._create_connection()
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
            if connection:
                try:
                    connection.close()
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
        """Initialize database with proper schema."""
        try:
            create_query = """
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
            self.execute_command(create_query)
            logger.info("Security-enhanced users table initialized")
        except DatabaseError as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def close_all_connections(self):
        """Close all connections (for graceful shutdown)."""
        self._closed = True

    def health_check(self) -> dict:
        """Health check for database availability."""
        try:
            with self.get_connection() as conn:
                conn.execute("SELECT 1")
                return {"status": "healthy", "database": "ready"}
        except DatabaseError as e:
            return {"status": "unhealthy", "error": str(e)}


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
