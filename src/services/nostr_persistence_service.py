"""
Nostr NIP-17 Message Persistence Service for 4S1T Agent AI

Handles database persistence for NIP-17 messages, contacts, approvals, and relay status.
Provides encrypted storage for message content at rest.
"""
import sqlite3
import json
import logging
import time
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import os

from database.connection import get_database_connection
from communication.nostr_nip17 import MessageType

logger = logging.getLogger(__name__)


class MessageTypeDB(Enum):
    """Message types for database storage."""
    SENT = "sent"
    RECEIVED = "received"


class DeliveryStatus(Enum):
    """Delivery status for messages."""
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    QUEUED = "queued"
    PROCESSING = "processing"


class ApprovalStatusDB(Enum):
    """Approval status for requests."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    EXPIRED = "expired"


@dataclass
class NostrMessageDB:
    """Database model for Nostr message."""
    id: Optional[int]
    event_id: str
    message_type: MessageTypeDB
    sender_npub: str
    recipient_npub: str
    content: str
    message_type_category: Optional[str]
    timestamp: float
    relay_url: Optional[str]
    event_created_at: Optional[int]
    delivery_status: DeliveryStatus
    delivery_attempts: int
    last_delivery_attempt: Optional[str]
    error_message: Optional[str]
    created_at: str
    updated_at: str


@dataclass
class NostrContactDB:
    """Database model for Nostr contact."""
    id: Optional[int]
    npub: str
    name: Optional[str]
    alias: Optional[str]
    contact_type: str
    is_blocked: bool
    is_trusted: bool
    notes: Optional[str]
    last_contacted: Optional[str]
    message_count_sent: int
    message_count_received: int
    created_at: str
    updated_at: str


@dataclass
class ApprovalRequestDB:
    """Database model for approval request."""
    id: str
    action: str
    details: Optional[str]
    requester_npub: Optional[str]
    approver_npub: Optional[str]
    status: ApprovalStatusDB
    request_event_id: Optional[str]
    response_event_id: Optional[str]
    request_timestamp: str
    response_timestamp: Optional[str]
    timeout_seconds: int
    expires_at: str
    context_data: Optional[str]
    created_at: str
    updated_at: str


@dataclass
class RelayStatusDB:
    """Database model for relay status."""
    id: Optional[int]
    relay_url: str
    is_connected: bool
    priority: int
    last_connection_attempt: Optional[str]
    last_successful_connection: Optional[str]
    last_disconnection: Optional[str]
    failure_count: int
    success_count: int
    messages_sent: int
    messages_received: int
    average_latency_ms: Optional[float]
    last_error: Optional[str]
    is_enabled: bool
    created_at: str
    updated_at: str


class NostrPersistenceService:
    """
    Service for persisting NIP-17 messages and related data.
    
    Features:
    - Encrypted message storage at rest
    - Message tracking and delivery status
    - Contact management
    - Approval workflow tracking
    - Relay health monitoring
    - Message queue with retry logic
    """
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize the persistence service.
        
        Args:
            encryption_key: Optional Fernet encryption key. If None, generates one.
        """
        self.db = get_database_connection()
        
        # Set up encryption for message content at rest
        if encryption_key:
            self.cipher = Fernet(encryption_key.encode())
        else:
            # Generate or load encryption key
            key_file = Path(".secrets") / "nostr_encryption.key"
            if key_file.exists():
                with open(key_file, 'rb') as f:
                    key = f.read()
                self.cipher = Fernet(key)
            else:
                key = Fernet.generate_key()
                key_file.parent.mkdir(exist_ok=True)
                with open(key_file, 'wb') as f:
                    f.write(key)
                os.chmod(key_file, 0o600)
                self.cipher = Fernet(key)
                logger.info("Generated new encryption key for Nostr message storage")
    
    def _encrypt_content(self, content: str) -> str:
        """Encrypt message content for storage at rest."""
        try:
            encrypted = self.cipher.encrypt(content.encode())
            return base64.b64encode(encrypted).decode()
        except Exception as e:
            logger.error(f"Failed to encrypt message content: {e}")
            # Fall back to storing unencrypted (logged for audit)
            logger.warning("Storing unencrypted content due to encryption failure")
            return content
    
    def _decrypt_content(self, encrypted_content: str) -> str:
        """Decrypt message content from storage."""
        try:
            encrypted = base64.b64decode(encrypted_content.encode())
            decrypted = self.cipher.decrypt(encrypted)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Failed to decrypt message content: {e}")
            # Return as-is if decryption fails
            return encrypted_content
    
    # ==================== Message Operations ====================
    
    def store_message(
        self,
        event_id: str,
        message_type: MessageTypeDB,
        sender_npub: str,
        recipient_npub: str,
        content: str,
        message_type_category: Optional[str] = None,
        timestamp: Optional[float] = None,
        relay_url: Optional[str] = None,
        event_created_at: Optional[int] = None
    ) -> int:
        """
        Store a Nostr message in the database.
        
        Args:
            event_id: Nostr event ID
            message_type: 'sent' or 'received'
            sender_npub: Sender's npub
            recipient_npub: Recipient's npub
            content: Message content (will be encrypted at rest)
            message_type_category: Type of message (chat, approval_request, etc.)
            timestamp: Unix timestamp (defaults to current time)
            relay_url: Relay used for this message
            event_created_at: Nostr event created_at timestamp
            
        Returns:
            Database row ID
        """
        if timestamp is None:
            timestamp = time.time()
        
        # Encrypt content at rest
        encrypted_content = self._encrypt_content(content)
        
        # Determine message_type_category from content if not provided
        if message_type_category is None:
            if content.lower().startswith(('approve:', 'approval:', 'request approval')):
                message_type_category = 'approval_request'
            elif content.lower().startswith(('approved', 'rejected', 'denied')):
                message_type_category = 'approval_response'
            elif content.lower().startswith(('/cmd ', '/command ', '!')):
                message_type_category = 'command'
            else:
                message_type_category = 'chat'
        
        query = """
            INSERT INTO nostr_messages (
                event_id, message_type, sender_npub, recipient_npub, content,
                message_type_category, timestamp, relay_url, event_created_at,
                delivery_status, delivery_attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
        
        params = (
            event_id,
            message_type.value,
            sender_npub,
            recipient_npub,
            encrypted_content,
            message_type_category,
            timestamp,
            relay_url,
            event_created_at,
            DeliveryStatus.DELIVERED.value,
            0
        )
        
        try:
            message_id = self.db.execute_command(query, params)
            logger.info(f"Stored Nostr message: {event_id} ({message_type.value})")
            
            # Update contact message counts
            self._update_contact_message_counts(sender_npub, recipient_npub, message_type)
            
            return message_id
        except sqlite3.IntegrityError:
            logger.warning(f"Message {event_id} already exists, updating...")
            return self.update_message_delivery_status(event_id, DeliveryStatus.DELIVERED)
    
    def update_message_delivery_status(
        self,
        event_id: str,
        status: DeliveryStatus,
        error_message: Optional[str] = None
    ) -> Optional[int]:
        """Update delivery status of a message."""
        query = """
            UPDATE nostr_messages
            SET delivery_status = ?,
                delivery_attempts = delivery_attempts + 1,
                last_delivery_attempt = CURRENT_TIMESTAMP,
                error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE event_id = ?
        """
        
        self.db.execute_command(query, (status.value, error_message, event_id))
        logger.info(f"Updated message {event_id} status to {status.value}")
        
        # Return the message ID
        result = self.db.execute_query(
            "SELECT id FROM nostr_messages WHERE event_id = ?",
            (event_id,)
        )
        return result[0]['id'] if result else None
    
    def get_message_by_event_id(self, event_id: str) -> Optional[NostrMessageDB]:
        """Get a message by its event ID."""
        query = "SELECT * FROM nostr_messages WHERE event_id = ?"
        result = self.db.execute_query(query, (event_id,))
        
        if not result:
            return None
        
        row = result[0]
        # Decrypt content
        decrypted_content = self._decrypt_content(row['content'])
        
        return NostrMessageDB(
            id=row['id'],
            event_id=row['event_id'],
            message_type=MessageTypeDB(row['message_type']),
            sender_npub=row['sender_npub'],
            recipient_npub=row['recipient_npub'],
            content=decrypted_content,
            message_type_category=row['message_type_category'],
            timestamp=row['timestamp'],
            relay_url=row['relay_url'],
            event_created_at=row['event_created_at'],
            delivery_status=DeliveryStatus(row['delivery_status']),
            delivery_attempts=row['delivery_attempts'],
            last_delivery_attempt=row['last_delivery_attempt'],
            error_message=row['error_message'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
    
    def get_messages(
        self,
        message_type: Optional[MessageTypeDB] = None,
        sender_npub: Optional[str] = None,
        recipient_npub: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[NostrMessageDB]:
        """Get messages with optional filters."""
        query = "SELECT * FROM nostr_messages WHERE 1=1"
        params = []
        
        if message_type:
            query += " AND message_type = ?"
            params.append(message_type.value)
        
        if sender_npub:
            query += " AND sender_npub = ?"
            params.append(sender_npub)
        
        if recipient_npub:
            query += " AND recipient_npub = ?"
            params.append(recipient_npub)
        
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        results = self.db.execute_query(query, tuple(params))
        
        messages = []
        for row in results:
            decrypted_content = self._decrypt_content(row['content'])
            messages.append(NostrMessageDB(
                id=row['id'],
                event_id=row['event_id'],
                message_type=MessageTypeDB(row['message_type']),
                sender_npub=row['sender_npub'],
                recipient_npub=row['recipient_npub'],
                content=decrypted_content,
                message_type_category=row['message_type_category'],
                timestamp=row['timestamp'],
                relay_url=row['relay_url'],
                event_created_at=row['event_created_at'],
                delivery_status=DeliveryStatus(row['delivery_status']),
                delivery_attempts=row['delivery_attempts'],
                last_delivery_attempt=row['last_delivery_attempt'],
                error_message=row['error_message'],
                created_at=row['created_at'],
                updated_at=row['updated_at']
            ))
        
        return messages
    
    # ==================== Contact Operations ====================
    
    def ensure_contact(self, npub: str, name: Optional[str] = None) -> NostrContactDB:
        """Ensure a contact exists, creating if necessary."""
        query = "SELECT * FROM nostr_contacts WHERE npub = ?"
        result = self.db.execute_query(query, (npub,))
        
        if result:
            contact_data = result[0]
            return NostrContactDB(**contact_data)
        
        # Create new contact
        insert_query = """
            INSERT INTO nostr_contacts (npub, name, created_at, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
        self.db.execute_command(insert_query, (npub, name or npub[:20]))
        
        # Return newly created contact
        return self.ensure_contact(npub, name)
    
    def update_contact(
        self,
        npub: str,
        name: Optional[str] = None,
        alias: Optional[str] = None,
        is_blocked: Optional[bool] = None,
        is_trusted: Optional[bool] = None,
        notes: Optional[str] = None
    ) -> bool:
        """Update contact information."""
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if alias is not None:
            updates.append("alias = ?")
            params.append(alias)
        
        if is_blocked is not None:
            updates.append("is_blocked = ?")
            params.append(1 if is_blocked else 0)
        
        if is_trusted is not None:
            updates.append("is_trusted = ?")
            params.append(1 if is_trusted else 0)
        
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)
        
        if not updates:
            return False
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(npub)
        
        query = f"UPDATE nostr_contacts SET {', '.join(updates)} WHERE npub = ?"
        self.db.execute_command(query, tuple(params))
        
        logger.info(f"Updated contact: {npub}")
        return True
    
    def _update_contact_message_counts(self, sender_npub: str, recipient_npub: str, message_type: MessageTypeDB):
        """Update message counts for contacts."""
        if message_type == MessageTypeDB.SENT:
            # Update recipient's received count
            query = """
                UPDATE nostr_contacts
                SET message_count_received = message_count_received + 1,
                    last_contacted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE npub = ?
            """
            self.db.execute_command(query, (recipient_npub,))
        else:
            # Update sender's sent count
            query = """
                UPDATE nostr_contacts
                SET message_count_sent = message_count_sent + 1,
                    last_contacted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE npub = ?
            """
            self.db.execute_command(query, (sender_npub,))
    
    def get_contacts(self, contact_type: Optional[str] = None) -> List[NostrContactDB]:
        """Get contacts with optional filter."""
        query = "SELECT * FROM nostr_contacts"
        params = []
        
        if contact_type:
            query += " WHERE contact_type = ?"
            params.append(contact_type)
        
        query += " ORDER BY last_contacted DESC NULLS LAST"
        
        results = self.db.execute_query(query, tuple(params))
        
        return [NostrContactDB(**row) for row in results]
    
    # ==================== Approval Request Operations ====================
    
    def store_approval_request(
        self,
        request_id: str,
        action: str,
        details: Optional[str],
        requester_npub: str,
        approver_npub: str,
        request_event_id: str,
        timeout_seconds: int = 300,
        context_data: Optional[Dict] = None
    ) -> bool:
        """Store an approval request."""
        from datetime import datetime, timedelta
        
        expires_at = datetime.utcnow() + timedelta(seconds=timeout_seconds)
        context_json = json.dumps(context_data) if context_data else None
        
        query = """
            INSERT INTO nostr_approval_requests (
                id, action, details, requester_npub, approver_npub, status,
                request_event_id, request_timestamp, timeout_seconds, expires_at,
                context_data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
        
        params = (
            request_id,
            action,
            details,
            requester_npub,
            approver_npub,
            ApprovalStatusDB.PENDING.value,
            request_event_id,
            timeout_seconds,
            expires_at.isoformat(),
            context_json
        )
        
        try:
            self.db.execute_command(query, params)
            logger.info(f"Stored approval request: {request_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store approval request: {e}")
            return False
    
    def update_approval_status(
        self,
        request_id: str,
        status: ApprovalStatusDB,
        response_event_id: Optional[str] = None
    ) -> bool:
        """Update approval request status."""
        query = """
            UPDATE nostr_approval_requests
            SET status = ?,
                response_event_id = ?,
                response_timestamp = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """
        
        self.db.execute_command(query, (status.value, response_event_id, request_id))
        logger.info(f"Updated approval {request_id} to {status.value}")
        return True
    
    def get_approval_request(self, request_id: str) -> Optional[ApprovalRequestDB]:
        """Get approval request by ID."""
        query = "SELECT * FROM nostr_approval_requests WHERE id = ?"
        result = self.db.execute_query(query, (request_id,))
        
        if not result:
            return None
        
        return ApprovalRequestDB(**result[0])
    
    def get_pending_approvals(self, approver_npub: Optional[str] = None) -> List[ApprovalRequestDB]:
        """Get pending approval requests."""
        query = "SELECT * FROM nostr_approval_requests WHERE status = ?"
        params = [ApprovalStatusDB.PENDING.value]
        
        if approver_npub:
            query += " AND approver_npub = ?"
            params.append(approver_npub)
        
        query += " ORDER BY request_timestamp ASC"
        
        results = self.db.execute_query(query, tuple(params))
        return [ApprovalRequestDB(**row) for row in results]
    
    # ==================== Relay Status Operations ====================
    
    def update_relay_status(
        self,
        relay_url: str,
        is_connected: bool,
        error_message: Optional[str] = None,
        latency_ms: Optional[float] = None
    ) -> bool:
        """Update relay connection status."""
        # Check if relay exists
        existing = self.db.execute_query(
            "SELECT * FROM nostr_relay_status WHERE relay_url = ?",
            (relay_url,)
        )
        
        if existing:
            row = existing[0]
            failure_count = row['failure_count']
            success_count = row['success_count']
            
            if is_connected:
                success_count += 1
                query = """
                    UPDATE nostr_relay_status
                    SET is_connected = ?,
                        last_successful_connection = CURRENT_TIMESTAMP,
                        success_count = ?,
                        average_latency_ms = ?,
                        last_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE relay_url = ?
                """
                params = (1, success_count, latency_ms, relay_url)
            else:
                failure_count += 1
                query = """
                    UPDATE nostr_relay_status
                    SET is_connected = ?,
                        last_disconnection = CURRENT_TIMESTAMP,
                        failure_count = ?,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE relay_url = ?
                """
                params = (0, failure_count, error_message, relay_url)
        else:
            # Insert new relay status
            priority = len(self.get_all_relay_status()) + 1
            query = """
                INSERT INTO nostr_relay_status (
                    relay_url, is_connected, priority, last_connection_attempt,
                    last_successful_connection, failure_count, success_count,
                    average_latency_ms, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, 0, 0, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
            if is_connected:
                params = (relay_url, 1, priority, latency_ms, None)
            else:
                params = (relay_url, 0, priority, None, error_message)
        
        # Update last_connection_attempt
        if is_connected:
            self.db.execute_command(
                "UPDATE nostr_relay_status SET last_connection_attempt = CURRENT_TIMESTAMP WHERE relay_url = ?",
                (relay_url,)
            )
        
        self.db.execute_command(query, params)
        return True
    
    def get_all_relay_status(self) -> List[RelayStatusDB]:
        """Get status of all relays."""
        query = "SELECT * FROM nostr_relay_status ORDER BY priority ASC"
        results = self.db.execute_query(query)
        return [RelayStatusDB(**row) for row in results]
    
    # ==================== Statistics ====================
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics."""
        stats = {}
        
        # Message counts
        stats['messages'] = self.db.execute_query("""
            SELECT 
                message_type,
                delivery_status,
                COUNT(*) as count
            FROM nostr_messages
            GROUP BY message_type, delivery_status
        """)
        
        # Contact counts
        stats['contacts'] = {
            'total': self.db.execute_query("SELECT COUNT(*) as count FROM nostr_contacts")[0]['count'],
            'trusted': self.db.execute_query("SELECT COUNT(*) as count FROM nostr_contacts WHERE is_trusted = 1")[0]['count'],
            'blocked': self.db.execute_query("SELECT COUNT(*) as count FROM nostr_contacts WHERE is_blocked = 1")[0]['count']
        }
        
        # Approval statistics
        stats['approvals'] = self.db.execute_query("""
            SELECT 
                status,
                COUNT(*) as count
            FROM nostr_approval_requests
            GROUP BY status
        """)
        
        # Relay status
        stats['relays'] = [
            {
                'url': r['relay_url'],
                'connected': bool(r['is_connected']),
                'success_rate': r['success_count'] / (r['success_count'] + r['failure_count']) if (r['success_count'] + r['failure_count']) > 0 else 0
            }
            for r in self.get_all_relay_status()
        ]
        
        return stats


# Global service instance
_persistence_service: Optional[NostrPersistenceService] = None


def get_nostr_persistence_service() -> NostrPersistenceService:
    """Get the global Nostr persistence service instance."""
    global _persistence_service
    if _persistence_service is None:
        _persistence_service = NostrPersistenceService()
    return _persistence_service
