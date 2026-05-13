"""
API Key management service for 4S1T Agent AI system.
Handles generation, validation, and revocation of API keys.
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import logging
import secrets
import hashlib

import sqlite3
from database.connection import get_database_connection
from services.exceptions import DatabaseError, ValidationError, AuthError
from utils.logger import setup_logger

logger = setup_logger(__name__)


class APIKeyService:
    """Service for managing API keys."""
    
    def __init__(self):
        """Initialize API key service."""
        self.db = get_database_connection()
        logger.info("API Key service initialized")
    
    def generate_api_key(self, user_id: str, name: str, description: Optional[str] = None,
                        scopes: str = "read", expires_days: Optional[int] = None,
                        provider_override: Optional[str] = None,
                        model_override: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate a new API key for a user.
        
        Args:
            user_id: User ID to associate with the key
            name: Human-readable name for the key
            description: Optional description
            scopes: Comma-separated list of scopes (e.g., 'read,write')
            expires_days: Number of days until expiration (None for no expiration)
            
        Returns:
            Dictionary containing the plain key (shown only once) and key metadata
            
        Raises:
            ValidationError: If input validation fails
            DatabaseError: If database operation fails
        """
        try:
            # Validate inputs
            if not user_id:
                raise ValidationError("User ID is required")
            if not name or len(name) < 3 or len(name) > 100:
                raise ValidationError("Name must be between 3 and 100 characters")
            
            # Generate a secure API key
            # Format: prefix.random_string
            prefix = "4s1t"
            random_part = secrets.token_urlsafe(32)
            plain_key = prefix + "_" + random_part
            
            # Hash the key for storage (only store hash, never the plain key)
            key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
            
            # Calculate expiration
            expires_at = None
            if expires_days:
                expires_at = (datetime.utcnow() + timedelta(days=expires_days)).isoformat()
            
            # Insert into database
            key_id = secrets.token_hex(16)
            insert_query = """
                INSERT INTO api_keys (id, user_id, key_hash, name, description, scopes,
                                     created_at, expires_at, is_active,
                                     provider_override, model_override)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, 1, ?, ?)
            """
            self.db.execute_command(insert_query,
                                   (key_id, user_id, key_hash, name, description, scopes,
                                    expires_at, provider_override, model_override))

            logger.info(f"API key created for user {user_id}: {name}")

            return {
                "id": key_id,
                "key": plain_key,  # Only returned once - must be saved by user
                "name": name,
                "description": description,
                "scopes": scopes,
                "created_at": datetime.utcnow().isoformat(),
                "expires_at": expires_at,
                "provider_override": provider_override,
                "model_override": model_override,
            }
            
        except sqlite3.IntegrityError as e:
            logger.error(f"Database integrity error creating API key: {e}")
            raise DatabaseError("Failed to create API key - database integrity error") from e
        except sqlite3.Error as e:
            logger.error(f"Database error creating API key: {e}")
            raise DatabaseError("Database service unavailable") from e
        except Exception as e:
            logger.error(f"Unexpected error creating API key: {e}", exc_info=True)
            raise AuthError("Internal server error") from e
    
    def validate_api_key(self, plain_key: str) -> Optional[Dict[str, Any]]:
        """
        Validate an API key and return associated user data.
        
        Args:
            plain_key: The plain API key to validate
            
        Returns:
            Dictionary containing user_id, scopes, and key metadata if valid,
            None if invalid or expired
        """
        try:
            # Hash the provided key
            key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
            
            # Look up the key
            query = """
                SELECT ak.*, u.role 
                FROM api_keys ak
                JOIN users u ON ak.user_id = u.id
                WHERE ak.key_hash = ? AND ak.is_active = 1
            """
            results = self.db.execute_query(query, (key_hash,))
            
            if not results:
                logger.warning("API key validation failed: key not found or inactive")
                return None
            
            key_data = results[0]
            
            # Check expiration
            if key_data["expires_at"]:
                expires = datetime.fromisoformat(key_data["expires_at"])
                if datetime.utcnow() > expires:
                    logger.warning(f"API key validation failed: key expired {key_data['id']}")
                    return None
            
            # Update last used timestamp
            update_query = "UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?"
            self.db.execute_command(update_query, (key_data["id"],))
            
            logger.info(f"API key validated for user {key_data['user_id']}")
            
            return {
                "key_id": key_data["id"],
                "user_id": key_data["user_id"],
                "role": key_data["role"],
                "scopes": key_data["scopes"],
                "name": key_data["name"],
                "provider_override": key_data.get("provider_override"),
                "model_override": key_data.get("model_override"),
            }
            
        except Exception as e:
            logger.error(f"Error validating API key: {e}", exc_info=True)
            return None
    
    def get_user_api_keys(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all API keys for a user (without the actual key values).
        
        Args:
            user_id: User ID to look up
            
        Returns:
            List of API key metadata dictionaries
        """
        try:
            query = """
                SELECT id, name, description, scopes, created_at, expires_at,
                       last_used_at, is_active, provider_override, model_override
                FROM api_keys
                WHERE user_id = ?
                ORDER BY created_at DESC
            """
            results = self.db.execute_query(query, (user_id,))

            keys = []
            for row in results:
                keys.append({
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "scopes": row["scopes"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                    "last_used_at": row["last_used_at"],
                    "is_active": bool(row["is_active"]),
                    "provider_override": row.get("provider_override"),
                    "model_override": row.get("model_override"),
                })
            
            return keys
            
        except Exception as e:
            logger.error(f"Error retrieving API keys for user {user_id}: {e}", exc_info=True)
            raise DatabaseError("Failed to retrieve API keys") from e
    
    def revoke_api_key(self, key_id: str, user_id: str) -> bool:
        """
        Revoke (deactivate) an API key.
        
        Args:
            key_id: ID of the key to revoke
            user_id: User ID requesting revocation (for authorization)
            
        Returns:
            True if successfully revoked, False otherwise
            
        Raises:
            ValidationError: If user doesn't own the key
            DatabaseError: If database operation fails
        """
        try:
            # Verify the key belongs to the user
            check_query = "SELECT user_id FROM api_keys WHERE id = ?"
            results = self.db.execute_query(check_query, (key_id,))
            
            if not results:
                raise ValidationError("API key not found")
            
            if results[0]["user_id"] != user_id:
                raise ValidationError("Not authorized to revoke this API key")
            
            # Revoke the key
            update_query = """
                UPDATE api_keys 
                SET is_active = 0 
                WHERE id = ? AND user_id = ?
            """
            self.db.execute_command(update_query, (key_id, user_id))
            
            logger.info(f"API key revoked: {key_id} by user {user_id}")
            return True
            
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error revoking API key {key_id}: {e}", exc_info=True)
            raise DatabaseError("Failed to revoke API key") from e
    
    def delete_api_key(self, key_id: str, user_id: str) -> bool:
        """
        Permanently delete an API key.
        
        Args:
            key_id: ID of the key to delete
            user_id: User ID requesting deletion (for authorization)
            
        Returns:
            True if successfully deleted, False otherwise
        """
        try:
            # Verify the key belongs to the user
            check_query = "SELECT user_id FROM api_keys WHERE id = ?"
            results = self.db.execute_query(check_query, (key_id,))
            
            if not results:
                raise ValidationError("API key not found")
            
            if results[0]["user_id"] != user_id:
                raise ValidationError("Not authorized to delete this API key")
            
            # Delete the key
            delete_query = "DELETE FROM api_keys WHERE id = ? AND user_id = ?"
            self.db.execute_command(delete_query, (key_id, user_id))
            
            logger.info(f"API key deleted: {key_id} by user {user_id}")
            return True
            
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error deleting API key {key_id}: {e}", exc_info=True)
            raise DatabaseError("Failed to delete API key") from e
    
    def update_api_key(self, key_id: str, user_id: str, name: Optional[str] = None,
                      description: Optional[str] = None) -> bool:
        """
        Update API key metadata.
        
        Args:
            key_id: ID of the key to update
            user_id: User ID requesting update (for authorization)
            name: New name (optional)
            description: New description (optional)
            
        Returns:
            True if successfully updated, False otherwise
        """
        try:
            # Build update query dynamically
            updates = []
            params = []
            
            if name is not None:
                updates.append("name = ?")
                params.append(name)
            
            if description is not None:
                updates.append("description = ?")
                params.append(description)
            
            if not updates:
                return True  # Nothing to update
            
            # Verify ownership and update
            params.extend([key_id, user_id])
            update_query = f"""
                UPDATE api_keys 
                SET {', '.join(updates)}
                WHERE id = ? AND user_id = ?
            """
            
            self.db.execute_command(update_query, tuple(params))
            logger.info(f"API key updated: {key_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating API key {key_id}: {e}", exc_info=True)
            raise DatabaseError("Failed to update API key") from e


# Global API key service instance
api_key_service: Optional[APIKeyService] = None


def get_api_key_service() -> APIKeyService:
    """
    Get singleton API key service instance.
    
    Returns:
        APIKeyService instance
    """
    global api_key_service
    if api_key_service is None:
        api_key_service = APIKeyService()
    return api_key_service
