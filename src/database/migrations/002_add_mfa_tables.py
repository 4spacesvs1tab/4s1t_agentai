"""
Database Migration: Add MFA/2FA Tables
Phase 2 Security Hardening
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

# Migration metadata
MIGRATION_ID = "002"
MIGRATION_NAME = "add_mfa_tables"
MIGRATION_DESCRIPTION = "Add tables for MFA/2FA authentication support"


def get_db_path():
    """Get database path from project root."""
    project_root = Path(__file__).parent.parent.parent.parent
    db_path = project_root / "4s1t_agent.db"
    return str(db_path)


def run_migration():
    """
    Run the MFA tables migration.
    Creates tables for user MFA settings, verification sessions, and audit logging.
    """
    db_path = get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {db_path}")
    print()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Create user_mfa table
        print("Creating user_mfa table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_mfa (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                mfa_enabled BOOLEAN NOT NULL DEFAULT 0,
                mfa_required BOOLEAN NOT NULL DEFAULT 1,
                totp_secret TEXT,
                authy_id TEXT,
                enrollment_complete BOOLEAN NOT NULL DEFAULT 0,
                enrollment_date TIMESTAMP,
                backup_codes TEXT,  -- JSON array of hashed backup codes
                last_verified TIMESTAMP,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                lockout_until TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        # Create index on user_id
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_mfa_user_id ON user_mfa(user_id)
        """)
        
        # Create index on mfa_enabled
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_mfa_enabled ON user_mfa(mfa_enabled)
        """)
        
        # Create mfa_sessions table for temporary verification sessions
        print("Creating mfa_sessions table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mfa_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_token TEXT NOT NULL UNIQUE,
                method TEXT NOT NULL,  -- 'totp', 'authy', 'backup'
                verified BOOLEAN NOT NULL DEFAULT 0,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                verified_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mfa_sessions_token ON mfa_sessions(session_token)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mfa_sessions_user ON mfa_sessions(user_id)
        """)
        
        # Create mfa_audit_log table
        print("Creating mfa_audit_log table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mfa_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,  -- 'enroll', 'verify', 'disable', 'backup_used'
                method TEXT,  -- 'totp', 'authy', 'backup'
                success BOOLEAN NOT NULL,
                details TEXT,  -- JSON with additional info
                ip_address TEXT,
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mfa_audit_user ON mfa_audit_log(user_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mfa_audit_action ON mfa_audit_log(action)
        """)
        
        # Update existing users to require MFA by default
        print("Enabling MFA requirement for existing users...")
        cursor.execute("""
            INSERT OR IGNORE INTO user_mfa (user_id, mfa_enabled, mfa_required, enrollment_complete)
            SELECT id, 0, 1, 0 FROM users WHERE id NOT IN (SELECT user_id FROM user_mfa)
        """)
        
        # Create migration history entry
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS migration_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_id TEXT NOT NULL UNIQUE,
                migration_name TEXT NOT NULL,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            )
        """)
        
        cursor.execute("""
            INSERT OR REPLACE INTO migration_history 
            (migration_id, migration_name, description, executed_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (MIGRATION_ID, MIGRATION_NAME, MIGRATION_DESCRIPTION))
        
        conn.commit()
        
        # Verify tables were created
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mfa_%'")
        tables = cursor.fetchall()
        print(f"\n✓ Created {len(tables)} MFA-related tables:")
        for table in tables:
            print(f"  - {table[0]}")
        
        print(f"\n✓ Migration {MIGRATION_ID} completed successfully!")
        print("\nNew tables:")
        print("  - user_mfa: Stores MFA settings and enrollment status")
        print("  - mfa_sessions: Temporary verification sessions")
        print("  - mfa_audit_log: MFA activity audit trail")
        
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"\n✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        conn.close()


if __name__ == "__main__":
    success = run_migration()
    exit(0 if success else 1)
