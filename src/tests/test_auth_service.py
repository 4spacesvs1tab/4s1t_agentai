"""
Test cases for authentication service.
"""
import pytest
import os
import tempfile
from unittest.mock import patch

from services.auth_service import AuthService
from core.security import SecurityManager


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    # Create a temporary database file
    temp_fd, temp_path = tempfile.mkstemp(suffix='.db')
    os.close(temp_fd)
    
    # Set the database URL to use the temporary file
    with patch('config.settings.settings.DATABASE_URL', temp_path):
        yield temp_path
    
    # Clean up the temporary file
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def auth_service(temp_db):
    """Create an authentication service instance for testing."""
    service = AuthService()
    # Reinitialize with the test database
    service.db.db_url = f"sqlite:///{temp_db}"
    service.db.connection = None
    # Initialize the database tables
    service.initialize_database()
    return service


def test_password_hashing():
    """Test password hashing and verification."""
    security_manager = SecurityManager()
    
    # Test hashing
    password = "test_pass_123"
    hashed = security_manager.hash_password(password)
    assert hashed != password  # Should not be the same
    
    # Test verification
    assert security_manager.verify_password(password, hashed) is True
    assert security_manager.verify_password("wrong_pass", hashed) is False


def test_create_user(auth_service):
    """Test user creation."""
    # Ensure clean state by deleting any existing test users
    try:
        auth_service.db.execute_command("DELETE FROM users", ())
    except Exception:
        pass  # Ignore if table doesn't exist yet or other database issues
    
    # Test successful user creation with strong password
    result = auth_service.create_user(
        username="testuser1",
        password="SecurePass123!"
    )
    assert result is True
    
    # Test another user creation with strong password
    result = auth_service.create_user(
        username="testuser2",
        password="AnotherPass456!"
    )
    assert result is True
    
    # Verify users were created
    users = auth_service.db.execute_query("SELECT * FROM users")
    assert len(users) == 2


def test_authenticate_user(auth_service):
    """Test user authentication."""
    # Ensure clean state by deleting any existing test users
    try:
        auth_service.db.execute_command("DELETE FROM users", ())
    except Exception:
        pass  # Ignore if table doesn't exist yet or other database issues
    
    # Create test users with username and password
    auth_service.create_user(username="user1", password="TestPass123!")
    auth_service.create_user(username="user2", password="AnotherPass456!")
    
    # Test successful authentication with first user
    user = auth_service.authenticate_user("user1", "TestPass123!")
    assert user is not None
    assert "id" in user
    assert "role" in user
    assert "password_hash" not in user  # Should not include password hash
    
    # Test successful authentication with second user
    user = auth_service.authenticate_user("user2", "AnotherPass456!")
    assert user is not None
    assert "id" in user
    
    # Test failed authentication with wrong password
    user = auth_service.authenticate_user("user1", "wrong_pass")
    assert user is None
    
    # Test failed authentication with non-existent user
    user = auth_service.authenticate_user("nonexistent", "TestPass123!")
    assert user is None
    
    # Test failed authentication with no users
    auth_service.db.execute_command("DELETE FROM users", ())
    user = auth_service.authenticate_user("user1", "TestPass123!")
    assert user is None


def test_initialize_database(auth_service):
    """Test database initialization."""
    # This should not raise an exception
    auth_service.initialize_database()
    
    # Check that the users table exists by trying to query it
    try:
        result = auth_service.db.execute_query("SELECT * FROM users LIMIT 1")
        assert isinstance(result, list)  # Should return a list
    except Exception as e:
        pytest.fail(f"Database initialization failed: {e}")
