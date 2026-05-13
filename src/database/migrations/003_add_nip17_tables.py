"""
Database Migration: Add NIP-17 Nostr Message Tables
NIP-17 encrypted messaging persistence for 4S1T Agent AI
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

# Migration metadata
MIGRATION_ID = "003"
MIGRATION_NAME = "add_nip17_tables"
MIGRATION_DESCRIPTION = "Add tables for NIP-17 encrypted message persistence and tracking"


def get_db_path():
    """Get database path from project root."""
    project_root = Path(__file__).parent.parent.parent.parent
    db_path = project_root / "4s1t_agent.db"
    return str(db_path)


def run_migration():
    """
    Run the NIP-17 message tables migration.
    Creates tables for storing sent/received NIP-17 encrypted messages,
    message tracking, and delivery status.
    """
    db_path = get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {db_path}")
    print()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Create nostr_messages table for storing all NIP-17 messages
        print("Creating nostr_messages table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nostr_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,  -- Nostr event ID
                message_type TEXT NOT NULL,  -- 'sent' or 'received'
                sender_npub TEXT NOT NULL,
                recipient_npub TEXT NOT NULL,
                content TEXT NOT NULL,  -- Encrypted content (will be encrypted at rest)
                message_type_category TEXT,  -- 'chat', 'approval_request', 'approval_response', 'command', 'unknown'
                timestamp REAL NOT NULL,  -- Unix timestamp
                relay_url TEXT,  -- Relay used for this message
                event_created_at INTEGER,  -- Nostr event created_at timestamp
                delivery_status TEXT DEFAULT 'pending',  -- 'pending', 'delivered', 'failed'
                delivery_attempts INTEGER DEFAULT 0,
                last_delivery_attempt TIMESTAMP,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for nostr_messages
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_messages_event_id ON nostr_messages(event_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_messages_message_type ON nostr_messages(message_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_messages_sender ON nostr_messages(sender_npub)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_messages_recipient ON nostr_messages(recipient_npub)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_messages_timestamp ON nostr_messages(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_messages_delivery_status ON nostr_messages(delivery_status)
        """)
        
        # Create nostr_contacts table for managing Nostr contacts
        print("Creating nostr_contacts table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nostr_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                npub TEXT NOT NULL UNIQUE,
                name TEXT,
                alias TEXT,
                contact_type TEXT DEFAULT 'user',  -- 'user', 'agent', 'system'
                is_blocked BOOLEAN NOT NULL DEFAULT 0,
                is_trusted BOOLEAN NOT NULL DEFAULT 0,
                notes TEXT,
                last_contacted TIMESTAMP,
                message_count_sent INTEGER DEFAULT 0,
                message_count_received INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for nostr_contacts
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_contacts_npub ON nostr_contacts(npub)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_contacts_type ON nostr_contacts(contact_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_contacts_trusted ON nostr_contacts(is_trusted)
        """)
        
        # Create nostr_approval_requests table for tracking approval workflows
        print("Creating nostr_approval_requests table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nostr_approval_requests (
                id TEXT PRIMARY KEY,  -- UUID request ID
                action TEXT NOT NULL,
                details TEXT,
                requester_npub TEXT,  -- Who requested approval (usually agent)
                approver_npub TEXT,  -- Who should approve (usually user)
                status TEXT DEFAULT 'pending',  -- 'pending', 'approved', 'rejected', 'timeout', 'expired'
                request_event_id TEXT,  -- Event ID of the request message
                response_event_id TEXT,  -- Event ID of the response message
                request_timestamp TIMESTAMP NOT NULL,
                response_timestamp TIMESTAMP,
                timeout_seconds INTEGER DEFAULT 300,  -- 5 minutes default
                expires_at TIMESTAMP NOT NULL,
                context_data TEXT,  -- JSON with additional context
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (requester_npub) REFERENCES nostr_contacts(npub),
                FOREIGN KEY (approver_npub) REFERENCES nostr_contacts(npub)
            )
        """)
        
        # Create indexes for nostr_approval_requests
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_approval_requests_id ON nostr_approval_requests(id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_approval_requests_status ON nostr_approval_requests(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_approval_requests_requester ON nostr_approval_requests(requester_npub)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_approval_requests_approver ON nostr_approval_requests(approver_npub)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_approval_requests_expires ON nostr_approval_requests(expires_at)
        """)
        
        # Create nostr_relay_status table for tracking relay health
        print("Creating nostr_relay_status table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nostr_relay_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relay_url TEXT NOT NULL UNIQUE,
                is_connected BOOLEAN NOT NULL DEFAULT 0,
                priority INTEGER DEFAULT 0,
                last_connection_attempt TIMESTAMP,
                last_successful_connection TIMESTAMP,
                last_disconnection TIMESTAMP,
                failure_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                messages_sent INTEGER DEFAULT 0,
                messages_received INTEGER DEFAULT 0,
                average_latency_ms REAL,
                last_error TEXT,
                is_enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for nostr_relay_status
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_relay_status_url ON nostr_relay_status(relay_url)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_relay_status_connected ON nostr_relay_status(is_connected)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_relay_status_priority ON nostr_relay_status(priority)
        """)
        
        # Create nostr_message_queue table for message queuing and retry
        print("Creating nostr_message_queue table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nostr_message_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                priority INTEGER DEFAULT 5,  -- 1=highest, 10=lowest
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                next_retry_at TIMESTAMP,
                status TEXT DEFAULT 'queued',  -- 'queued', 'processing', 'completed', 'failed'
                locked_at TIMESTAMP,
                locked_by TEXT,
                error_log TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (message_id) REFERENCES nostr_messages(id) ON DELETE CASCADE
            )
        """)
        
        # Create indexes for nostr_message_queue
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_queue_status ON nostr_message_queue(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_queue_priority ON nostr_message_queue(priority)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_nostr_queue_next_retry ON nostr_message_queue(next_retry_at)
        """)
        
        # Create migration history entry
        cursor.execute("""
            INSERT OR REPLACE INTO migration_history 
            (migration_id, migration_name, description, executed_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (MIGRATION_ID, MIGRATION_NAME, MIGRATION_DESCRIPTION))
        
        conn.commit()
        
        # Verify tables were created
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'nostr_%'")
        tables = cursor.fetchall()
        print(f"\n✓ Created {len(tables)} NIP-17 related tables:")
        for table in tables:
            print(f"  - {table[0]}")
        
        print(f"\n✓ Migration {MIGRATION_ID} completed successfully!")
        print("\nNew tables:")
        print("  - nostr_messages: Store all NIP-17 sent/received messages")
        print("  - nostr_contacts: Manage Nostr contact list and trust settings")
        print("  - nostr_approval_requests: Track approval workflow state")
        print("  - nostr_relay_status: Monitor relay health and performance")
        print("  - nostr_message_queue: Queue messages for delivery with retry logic")
        print("\nFeatures:")
        print("  - Encrypted message storage (content encrypted at rest)")
        print("  - Message tracking with delivery status")
        print("  - Contact management with trust/block settings")
        print("  - Approval workflow tracking")
        print("  - Relay health monitoring")
        print("  - Message queue with retry mechanism")
        
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
