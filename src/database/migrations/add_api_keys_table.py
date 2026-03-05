"""
Database migration to add API keys table.

This migration creates the api_keys table for managing user API keys.
"""
import sqlite3
import logging
from database.connection import get_database_connection

logger = logging.getLogger(__name__)


def migrate():
    """Create API keys table."""
    try:
        db = get_database_connection()
        
        # Create API keys table
        create_table_query = """
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                scopes TEXT DEFAULT 'read',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                last_used_at TEXT,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """
        
        db.execute_command(create_table_query)
        
        # Create index for faster lookups
        create_index_query = """
            CREATE INDEX IF NOT EXISTS idx_api_keys_user_id 
            ON api_keys(user_id)
        """
        db.execute_command(create_index_query)
        
        create_index_hash_query = """
            CREATE INDEX IF NOT EXISTS idx_api_keys_hash 
            ON api_keys(key_hash)
        """
        db.execute_command(create_index_hash_query)
        
        logger.info("API keys table created successfully")
        return True
        
    except sqlite3.Error as e:
        logger.error(f"Failed to create API keys table: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during migration: {e}")
        raise


def rollback():
    """Rollback the migration - drop API keys table."""
    try:
        db = get_database_connection()
        db.execute_command("DROP TABLE IF EXISTS api_keys")
        logger.info("API keys table dropped successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to rollback API keys table: {e}")
        raise


if __name__ == "__main__":
    migrate()
