"""
Per-path DatabaseConnection registry for infrastructure/sqlite repositories.

WHY: The sqlite repositories in this package previously opened raw connections
directly, bypassing the app's central DatabaseConnection pool (WAL mode, busy
timeout, semaphore limiting, 0o600 permission enforcement). This helper routes
every repository connection through DatabaseConnection.get_connection() without
requiring repositories to know about the pool internals.

THREAD SAFETY: DatabaseConnection.get_connection() creates a new sqlite3
connection per call and closes it in its finally block — no shared connection
state. Safe to call from run_in_executor thread-pool workers.

PATTERN: repositories call
    with get_db_connection(self._db_path) as conn:
        rows = conn.execute("SELECT ...", params).fetchall()
        conn.commit()   # for writes only

The yielded conn is a sqlite3.Connection configured with:
  - row_factory = sqlite3.Row  (set by DatabaseConnection._create_connection)
  - PRAGMA journal_mode=WAL
  - PRAGMA busy_timeout=5000
  - PRAGMA foreign_keys=ON
"""
import threading
from contextlib import contextmanager
from typing import Generator
import sqlite3

from database.connection import DatabaseConnection

_registry: dict[str, DatabaseConnection] = {}
_registry_lock = threading.Lock()


def _get_or_create(db_path: str) -> DatabaseConnection:
    """Return the shared DatabaseConnection for *db_path*, creating it once."""
    with _registry_lock:
        if db_path not in _registry:
            # sqlite:///  +  absolute path  →  sqlite:////absolute/path (4 slashes)
            # DatabaseConnection strips the "sqlite:///" prefix to recover the path.
            _registry[db_path] = DatabaseConnection(db_url=f"sqlite:///{db_path}")
        return _registry[db_path]


@contextmanager
def get_db_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Context manager: yield a configured sqlite3.Connection from the shared pool.

    Usage::

        with get_db_connection(self._db_path) as conn:
            rows = conn.execute("SELECT ...", params).fetchall()
    """
    with _get_or_create(db_path).get_connection() as conn:
        yield conn
