"""
Test configuration and fixtures for 4S1T Agent AI.
Sets up environment variables and test configurations.
"""
import os
import tempfile
import pytest
from unittest.mock import patch


# Set up test environment variables before importing any modules
os.environ["SECRET_KEY"] = "test_secret_key_12345678901234567890123456789012"
os.environ["DATABASE_URL"] = "sqlite:///test.db"
os.environ["ALLOWED_ORIGINS"] = '["http://localhost:3000"]'


def pytest_configure(config):
    """Configure pytest with environment variables."""
    os.environ["SECRET_KEY"] = "test_secret_key_12345678901234567890123456789012"
    os.environ["DATABASE_URL"] = "sqlite:///test.db"
    os.environ["ALLOWED_ORIGINS"] = '["http://localhost:3000"]'


@pytest.fixture(autouse=True)
def setup_test_env():
    """
    Automatically set up test environment for all tests.
    This fixture runs before each test.
    """
    with patch.dict(os.environ, {
        "SECRET_KEY": "test_secret_key_12345678901234567890123456789012",
        "DATABASE_URL": "sqlite:///test.db",
        "ALLOWED_ORIGINS": '["http://localhost:3000"]',
        "DEBUG": "true"
    }):
        yield


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    temp_fd, temp_path = tempfile.mkstemp(suffix='.db')
    os.close(temp_fd)
    
    # Clean up any existing database file
    if os.path.exists(temp_path):
        os.unlink(temp_path)
    
    with patch('config.settings.settings.DATABASE_URL', f"sqlite:///{temp_path}"):
        yield temp_path
    
    # Clean up after test
    if os.path.exists(temp_path):
        try:
            os.unlink(temp_path)
        except:
            pass
