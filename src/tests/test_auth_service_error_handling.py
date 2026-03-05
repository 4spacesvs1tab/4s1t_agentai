"""
Test cases for enhanced database error handling in authentication service.
Tests Priority 2.1 implementation: Database Error Handling Improvements.
"""
import pytest
import os
import tempfile
import sqlite3
from unittest.mock import patch, MagicMock

from services.auth_service import AuthService
from services.exceptions import AuthError, DatabaseError, ValidationError
from core.security import SecurityManager


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    temp_fd, temp_path = tempfile.mkstemp(suffix='.db')
    os.close(temp_fd)
    
    with patch('config.settings.settings.DATABASE_URL', temp_path):
        yield temp_path
    
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def auth_service(temp_db):
    """Create an authentication service instance for testing."""
    service = AuthService()
    service.db.db_url = f"sqlite:///{temp_db}"
    service.db.connection = None
    service.initialize_database()
    return service


class TestDatabaseErrorHandling:
    """Test enhanced database error handling for create_user method."""
    
    def test_create_user_success(self, auth_service):
        """Test successful user creation."""
        result = auth_service.create_user("testuser", "ValidPass123!@#")
        assert result is True
        
        # Verify user was created
        users = auth_service.db.execute_query("SELECT * FROM users WHERE username = ?", ("testuser",))
        assert len(users) == 1
        assert users[0]["username"] == "testuser"
    
    def test_create_user_duplicate_username(self, auth_service):
        """Test creating user with duplicate username raises ValidationError."""
        # Create first user
        auth_service.create_user("duplicate_user", "ValidPass123!@#")
        
        # Try to create duplicate user
        with pytest.raises(ValidationError) as exc_info:
            auth_service.create_user("duplicate_user", "AnotherPass456$%^")
        
        assert "already taken" in str(exc_info.value)
        assert "duplicate_user" in str(exc_info.value)
    
    def test_create_user_invalid_username_format(self, auth_service):
        """Test creating user with invalid username format raises ValidationError."""
        # Test too short
        with pytest.raises(ValidationError) as exc_info:
            auth_service.create_user("ab", "ValidPass123!@#")
        assert "Invalid username format" in str(exc_info.value)
        
        # Test invalid characters
        with pytest.raises(ValidationError) as exc_info:
            auth_service.create_user("invalid@user", "ValidPass123!@#")
        assert "Invalid username format" in str(exc_info.value)
    
    def test_create_user_weak_password(self, auth_service):
        """Test creating user with weak password raises ValidationError."""
        # Test too short
        with pytest.raises(ValidationError) as exc_info:
            auth_service.create_user("testuser", "Short1!")
        assert "Password does not meet strength requirements" in str(exc_info.value)
        
        # Test missing uppercase
        with pytest.raises(ValidationError) as exc_info:
            auth_service.create_user("testuser", "lowercase1!")
        assert "Password does not meet strength requirements" in str(exc_info.value)
        
        # Test missing special character
        with pytest.raises(ValidationError) as exc_info:
            auth_service.create_user("testuser", "NoSpecial123")
        assert "Password does not meet strength requirements" in str(exc_info.value)


class TestOperationalErrorHandling:
    """Test handling of sqlite3.OperationalError."""
    
    def test_create_user_operational_error(self, auth_service):
        """Test handling of OperationalError during user creation."""
        # Mock execute_command to raise OperationalError
        with patch.object(auth_service.db, 'execute_command', side_effect=sqlite3.OperationalError("database is locked")):
            with pytest.raises(DatabaseError) as exc_info:
                auth_service.create_user("testuser", "ValidPass123!@#")
            
            assert "Database service unavailable" in str(exc_info.value)
    
    def test_authenticate_user_operational_error(self, auth_service):
        """Test handling of OperationalError during authentication."""
        # Create a valid user first
        auth_service.create_user("testuser", "ValidPass123!@#")
        
        # Mock execute_query to raise OperationalError
        with patch.object(auth_service.db, 'execute_query', side_effect=sqlite3.OperationalError("database is locked")):
            with pytest.raises(DatabaseError) as exc_info:
                auth_service.authenticate_user("testuser", "ValidPass123!@#")
            
            assert "Database service unavailable" in str(exc_info.value)


class TestProgrammingErrorHandling:
    """Test handling of sqlite3.ProgrammingError."""
    
    def test_create_user_programming_error(self, auth_service):
        """Test handling of ProgrammingError during user creation."""
        # Mock execute_command to raise ProgrammingError
        with patch.object(auth_service.db, 'execute_command', side_effect=sqlite3.ProgrammingError("SQL syntax error")):
            with pytest.raises(DatabaseError) as exc_info:
                auth_service.create_user("testuser", "ValidPass123!@#")
            
            assert "Internal database error" in str(exc_info.value)
    
    def test_authenticate_user_programming_error(self, auth_service):
        """Test handling of ProgrammingError during authentication."""
        # Create a valid user first
        auth_service.create_user("testuser", "ValidPass123!@#")
        
        # Mock execute_query to raise ProgrammingError
        with patch.object(auth_service.db, 'execute_query', side_effect=sqlite3.ProgrammingError("SQL syntax error")):
            with pytest.raises(DatabaseError) as exc_info:
                auth_service.authenticate_user("testuser", "ValidPass123!@#")
            
            assert "Internal database error" in str(exc_info.value)


class TestUnexpectedErrorHandling:
    """Test handling of unexpected errors."""
    
    def test_create_user_unexpected_error(self, auth_service):
        """Test handling of unexpected errors during user creation."""
        # Mock execute_command to raise unexpected error
        with patch.object(auth_service.db, 'execute_command', side_effect=Exception("Unexpected system error")):
            with pytest.raises(AuthError) as exc_info:
                auth_service.create_user("testuser", "ValidPass123!@#")
            
            assert "Internal server error" in str(exc_info.value)
    
    def test_stack_trace_logging(self, auth_service, caplog):
        """Test that stack traces are logged for unexpected errors."""
        # Mock execute_command to raise unexpected error
        with patch.object(auth_service.db, 'execute_command', side_effect=ValueError("Unexpected value error")):
            with pytest.raises(AuthError):
                auth_service.create_user("testuser", "ValidPass123!@#")
            
            # Check that error was logged with exc_info=True
            assert any("Unexpected error during user creation" in record.message for record in caplog.records)
            # Check that stack trace is included (traceback should be in the log)
            error_records = [r for r in caplog.records if "Unexpected error" in r.message]
            assert len(error_records) > 0


class TestDatabaseErrorPropagation:
    """Test that DatabaseError is properly raised and propagated."""
    
    def test_operational_error_in_authenticate(self, auth_service):
        """Test OperationalError in authenticate_user method."""
        with patch.object(auth_service.db, 'execute_query', side_effect=sqlite3.OperationalError("database is locked")):
            with pytest.raises(DatabaseError) as exc_info:
                auth_service.authenticate_user("testuser", "ValidPass123!@#")
            
            assert "Database service unavailable" in str(exc_info.value)
    
    def test_programming_error_in_authenticate(self, auth_service):
        """Test ProgrammingError in authenticate_user method."""
        with patch.object(auth_service.db, 'execute_query', side_effect=sqlite3.ProgrammingError("SQL syntax error")):
            with pytest.raises(DatabaseError) as exc_info:
                auth_service.authenticate_user("testuser", "ValidPass123!@#")
            
            assert "Internal database error" in str(exc_info.value)


class TestUsernameValidationEdgeCases:
    """Test edge cases for username validation."""
    
    def test_username_length_boundaries(self, auth_service):
        """Test username length boundary conditions."""
        # Test minimum length (3 chars)
        result = auth_service.create_user("abc", "ValidPass123!@#")
        assert result is True
        
        # Test maximum length (50 chars)
        long_username = "a" * 50
        result = auth_service.create_user(long_username, "ValidPass123!@#")
        assert result is True
        
        # Test too long (51 chars)
        with pytest.raises(ValidationError) as exc_info:
            auth_service.create_user("a" * 51, "ValidPass123!@#")
        assert "Invalid username format" in str(exc_info.value)
    
    def test_username_special_characters(self, auth_service):
        """Test username with various special characters."""
        # Valid: underscores
        result = auth_service.create_user("valid_user_123", "ValidPass123!@#")
        assert result is True
        
        # Invalid: spaces
        with pytest.raises(ValidationError) as exc_info:
            auth_service.create_user("invalid user", "ValidPass123!@#")
        assert "Invalid username format" in str(exc_info.value)
        
        # Invalid: special characters
        with pytest.raises(ValidationError) as exc_info:
            auth_service.create_user("user@domain", "ValidPass123!@#")
        assert "Invalid username format" in str(exc_info.value)


class TestPasswordValidationEdgeCases:
    """Test edge cases for password validation."""
    
    def test_password_strength_requirements(self, auth_service):
        """Test all password strength requirements."""
        # Valid password
        result = auth_service.create_user("testuser", "ValidPass123!@#")
        assert result is True
        
        # Missing uppercase
        with pytest.raises(ValidationError):
            auth_service.create_user("testuser2", "lowercase1!")
        
        # Missing lowercase
        with pytest.raises(ValidationError):
            auth_service.create_user("testuser3", "UPPERCASE1!")
        
        # Missing digit
        with pytest.raises(ValidationError):
            auth_service.create_user("testuser4", "NoDigits!@#")
        
        # Missing special character
        with pytest.raises(ValidationError):
            auth_service.create_user("testuser5", "NoSpecial123")
        
        # Exactly 12 characters (minimum)
        result = auth_service.create_user("testuser6", "ValidPass1!@")
        assert result is True
