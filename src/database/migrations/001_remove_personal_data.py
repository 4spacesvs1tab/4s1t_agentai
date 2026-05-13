"""
Migration 001: Remove personal data fields from users table

This migration removes email and full_name columns from the users table
to comply with data minimization requirements and fix schema inconsistencies.

**IMPORTANT**: Run this migration BEFORE deploying the updated application code.

**BACKUP REQUIRED**: This migration modifies your database schema. 
Always backup your database before running migrations.

Usage:
    python src/database/migrations/001_remove_personal_data.py
"""

import sqlite3
import sys
import os
import shutil
from datetime import datetime

# Add parent directories to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config.settings import settings


def backup_database(db_path: str) -> str:
    """
    Create a backup of the database before migration.
    
    Args:
        db_path: Path to the database file
        
    Returns:
        Path to the backup file
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup.{timestamp}"
    
    try:
        shutil.copy2(db_path, backup_path)
        print(f"✅ Database backed up to: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"❌ Failed to backup database: {e}")
        sys.exit(1)


def check_table_schema(conn: sqlite3.Connection) -> dict:
    """
    Check the current schema of the users table.
    
    Args:
        conn: Database connection
        
    Returns:
        Dictionary with schema information
    """
    cursor = conn.cursor()
    
    # Check if users table exists
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='users'
    """)
    
    if not cursor.fetchone():
        return {"exists": False, "columns": []}
    
    # Get column information
    cursor.execute("PRAGMA table_info(users)")
    columns = cursor.fetchall()
    
    column_names = [col[1] for col in columns]
    
    return {
        "exists": True,
        "columns": column_names,
        "has_email": "email" in column_names,
        "has_full_name": "full_name" in column_names
    }


def migrate_database(conn: sqlite3.Connection, schema_info: dict):
    """
    Perform the migration to remove email and full_name columns.
    
    Args:
        conn: Database connection
        schema_info: Schema information from check_table_schema
    """
    cursor = conn.cursor()
    
    if not schema_info["exists"]:
        print("ℹ️  Users table does not exist. Migration not needed.")
        return
    
    has_email = schema_info["has_email"]
    has_full_name = schema_info["has_full_name"]
    
    if not has_email and not has_full_name:
        print("✅ Users table already has correct schema. Migration not needed.")
        return
    
    print(f"🔧 Migrating users table schema...")
    print(f"   - Has email column: {has_email}")
    print(f"   - Has full_name column: {has_full_name}")
    
    try:
        # Create new table with correct schema
        print("   Creating new users table...")
        cursor.execute("""
            CREATE TABLE users_new (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login TEXT
            )
        """)
        
        # Copy data from old table to new table
        print("   Copying data to new table...")
        
        # Build column list for INSERT
        base_columns = ["id", "username", "password_hash", "role", "is_active", "created_at", "last_login"]
        
        insert_columns = ", ".join(base_columns)
        select_columns = ", ".join(base_columns)
        
        cursor.execute(f"""
            INSERT INTO users_new ({insert_columns})
            SELECT {select_columns}
            FROM users
        """)
        
        rows_copied = cursor.rowcount
        print(f"   ✅ Copied {rows_copied} user records")
        
        # Drop old table
        print("   Dropping old users table...")
        cursor.execute("DROP TABLE users")
        
        # Rename new table
        print("   Renaming new table...")
        cursor.execute("ALTER TABLE users_new RENAME TO users")
        
        # Commit the transaction
        conn.commit()
        print("✅ Migration completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Migration failed: {e}")
        raise


def verify_migration(conn: sqlite3.Connection):
    """
    Verify that the migration was successful.
    
    Args:
        conn: Database connection
    """
    cursor = conn.cursor()
    
    # Check final schema
    cursor.execute("PRAGMA table_info(users)")
    columns = cursor.fetchall()
    
    column_names = [col[1] for col in columns]
    expected_columns = ["id", "username", "password_hash", "role", "is_active", "created_at", "last_login"]
    
    print("\n📋 Verifying migration...")
    print(f"   Expected columns: {expected_columns}")
    print(f"   Actual columns:   {column_names}")
    
    # Check for unwanted columns
    unwanted_columns = ["email", "full_name"]
    for col in unwanted_columns:
        if col in column_names:
            print(f"❌ Migration verification failed: unwanted column '{col}' still exists")
            return False
    
    # Check for missing required columns
    for col in expected_columns:
        if col not in column_names:
            print(f"❌ Migration verification failed: required column '{col}' is missing")
            return False
    
    # Check row count
    cursor.execute("SELECT COUNT(*) FROM users")
    row_count = cursor.fetchone()[0]
    print(f"   Total users in table: {row_count}")
    
    print("✅ Migration verification passed!")
    return True


def main():
    """Main migration execution."""
    print("=" * 60)
    print("4S1T Agent AI - Database Migration 001")
    print("Remove Personal Data Fields from Users Table")
    print("=" * 60)
    print()
    
    # Get database path from settings
    db_url = settings.DATABASE_URL
    if not db_url.startswith("sqlite:///"):
        print("❌ This migration only supports SQLite databases")
        sys.exit(1)
    
    db_path = db_url.replace("sqlite:///", "")
    
    if not os.path.exists(db_path):
        print(f"ℹ️  Database file does not exist: {db_path}")
        print("   Migration not needed (fresh installation)")
        sys.exit(0)
    
    print(f"📁 Database: {db_path}")
    print()
    
    # Confirm backup
    print("⚠️  IMPORTANT: This migration will modify your database schema.")
    print("   A backup will be created automatically.")
    print()
    
    response = input("Do you want to continue with the migration? (yes/no): ")
    if response.lower() != "yes":
        print("Migration cancelled.")
        sys.exit(0)
    
    print()
    
    try:
        # Create backup
        backup_path = backup_database(db_path)
        
        # Connect to database
        conn = sqlite3.connect(db_path)
        
        # Check current schema
        schema_info = check_table_schema(conn)
        
        if not schema_info["exists"]:
            print("ℹ️  Users table does not exist. Creating with correct schema...")
            # Create the table with correct schema
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login TEXT
                )
            """)
            conn.commit()
            print("✅ Users table created with correct schema")
        else:
            # Perform migration
            migrate_database(conn, schema_info)
        
        # Verify migration
        verify_migration(conn)
        
        # Close connection
        conn.close()
        
        print()
        print("=" * 60)
        print("✅ MIGRATION COMPLETED SUCCESSFULLY")
        print("=" * 60)
        print()
        print(f"📁 Backup created at: {backup_path}")
        print(f"💾 Database updated: {db_path}")
        print()
        print("Next steps:")
        print("1. Test your application with the updated database")
        print("2. If any issues occur, restore from backup:")
        print(f"   cp {backup_path} {db_path}")
        print()
        
    except Exception as e:
        print()
        print("=" * 60)
        print("❌ MIGRATION FAILED")
        print("=" * 60)
        print()
        print(f"Error: {e}")
        print()
        print("Your database has been rolled back to its original state.")
        print(f"Backup is available at: {backup_path}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
