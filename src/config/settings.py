"""
Settings module for 4S1T Agent AI system.
Handles configuration from environment variables, .env files, and defaults.
"""
import re
import os
from typing import List, Optional
import urllib.parse
import logging
import warnings

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, validator, model_validator

from services.exceptions import ConfigurationError, ValidationError


class SecuritySettings:
    """Security-specific configuration validation."""
    
    @classmethod
    def validate_secret_key(cls, key: str) -> str:
        """Validate JWT secret key strength and security."""
        if not key or key.strip() == "your-secret-key-change-in-production":
            raise ConfigurationError(
                "SECRET_KEY must be changed from default for production use"
            )
        
        if len(key) < 32:
            raise ConfigurationError(
                "SECRET_KEY must be at least 32 characters for security"
            )
        
        # Check for basic key strength
        if key.isalpha() or key.isdigit() or key.isspace():
            raise ConfigurationError(
                "SECRET_KEY must contain mixed character types (letters, numbers, symbols)"
            )
            
        return key
    
    @classmethod
    def validate_jwt_algorithm(cls, algorithm: str) -> str:
        """Validate JWT algorithm security."""
        allowed_algorithms = ["HS256", "HS384", "HS512", "RS256", "RS384", "RS512"]
        if algorithm not in allowed_algorithms:
            raise ConfigurationError(
                f"ALGORITHM must be one of {allowed_algorithms}, got: {algorithm}"
            )
        return algorithm


class DatabaseSettings:
    """Database-specific configuration validation."""
    
    @classmethod
    def validate_sqlite_url(cls, url: str) -> str:
        """Validate SQLite database URL."""
        if not url.startswith("sqlite:///"):
            raise ConfigurationError(
                "DATABASE_URL must be a SQLite URL starting with 'sqlite:///...'"
            )
        
        # Extract path and validate file accessibility
        db_path = url.replace("sqlite:///", "")
        if ":memory:" in db_path.lower():
            return url
        
        # Check if directory exists for file-based databases
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except OSError as e:
                raise ConfigurationError(f"Cannot create database directory: {e}")
        
        # Check file create permission
        try:
            with open(db_path, "a"):  # Test write access
                pass
        except (OSError, IOError) as e:
            raise ConfigurationError(f"Database file not writable: {db_path} - {e}")
        
        return url
    
    @classmethod
    def validate_url_format(cls, url: str, expected_type: str = "database") -> str:
        """Generic URL format validation."""
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme or not parsed.path:
            raise ConfigurationError(f"Invalid {expected_type} URL format: {url}")
        return url


class RedisSettings:
    """Redis-specific configuration validation."""
    
    @classmethod
    def validate_redis_url(cls, url: str) -> str:
        """Validate Redis connection URL."""
        parsed = urllib.parse.urlparse(url)
        
        if not url.startswith("redis://"):
            raise ConfigurationError("REDIS_URL must use redis:// scheme")
        
        try:
            hostname = parsed.hostname or "localhost"
            port = int(parsed.port) if parsed.port else 6379
            
            # Validate hostname format
            if not re.match(r'^[a-zA-Z0-9.-]+$', hostname):
                raise ConfigurationError(f"Invalid Redis hostname: {hostname}")
            
            # Validate port range
            if port < 1 or port > 65535:
                raise ConfigurationError(f"Invalid Redis port: {port}")
                
        except (ValueError, AttributeError) as e:
            raise ConfigurationError(f"Invalid Redis URL format: {url} - {e}")
        
        return url


class APISettings:
    """API and network configuration validation."""
    
    @classmethod
    def validate_port(cls, port: int) -> int:
        """Validate API port configuration."""
        if port < 1 or port > 65535:
            raise ConfigurationError(f"PORT must be between 1-65535, got: {port}")
        return port
    
    @classmethod
    def validate_host(cls, host: str) -> str:
        """Validate host binding configuration."""
        # Allow special values and standard IP formats
        allowed_special = ["0.0.0.0", "127.0.0.1", "localhost", "::", "::1"]
        
        if host in allowed_special:
            return host
        
        # Validate IP address format
        import ipaddress
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            # Validate hostname format
            if re.match(r'^[a-zA-Z0-9.-]+$', host):
                return host
            raise ConfigurationError(f"Invalid HOST format: {host}")
    
    @classmethod
    def validate_cors_origins(cls, origins: List[str]) -> List[str]:
        """Validate CORS origins configuration."""
        if not origins:
            raise ConfigurationError("ALLOWED_ORIGINS cannot be empty")
        
        allowed_origins = []
        for origin in origins:
            if origin == "*":
                warnings.warn("Allowing all CORS origins - use only in development", RuntimeWarning)
                return origins
            
            # Validate origin format
            parsed = urllib.parse.urlparse(origin)
            if not parsed.scheme or not parsed.netloc:
                raise ConfigurationError(f"Invalid CORS origin format: {origin}")
            
            allowed_origins.append(origin)
        
        # Remove duplicates while preserving order
        seen = set()
        return [x for x in allowed_origins if not (x in seen or seen.add(x))]


class Settings(BaseSettings):
    """
    Application settings class with comprehensive validation.
    
    Priority 2 Security Enhancement: Enhanced configuration validation
    Provides defense-in-depth against configuration vulnerabilities.
    """
    
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="allow"  # Allow unknown config keys for backward compatibility
    )
    
    # Application settings
    APP_NAME: str = Field(
        default="4S1T Agent AI", 
        description="Application name for logging and branding"
    )
    DEBUG: bool = Field(
        default=False, 
        description="Debug mode flag - enable only in development"
    )
    HOST: str = Field(
        default="127.0.0.1",  # Changed default for security
        description="Host binding for API service"
    )
    PORT: int = Field(
        default=8000,
        description="Port number for API service"
    )
    
    # Security settings
    SECRET_KEY: str = Field(
        description="JWT secret key - must be strong for production"
    )
    ALGORITHM: str = Field(
        default="HS256",
        description="JWT signing algorithm"
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=60,
        description="Access token expiration in minutes"
    )
    
    # CORS settings
    ALLOWED_ORIGINS: List[str] = Field(
        default=["http://localhost:5000", "http://localhost:8000"],
        description="Allowed CORS origins for API access"
    )
    
    # Database settings
    DATABASE_URL: str = Field(
        default="sqlite:///./data/agent.db",
        description="SQLite database URL"
    )
    
    # ChromaDB settings
    CHROMA_HOST: str = Field(
        default="localhost",
        description="ChromaDB service host"
    )
    CHROMA_PORT: int = Field(
        default=8001,
        description="ChromaDB service port"
    )
    CHROMA_PERSIST_DIR: str = Field(
        default="./data/chroma",
        description="ChromaDB persistence directory"
    )
    
    # Redis settings
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL"
    )
    
    # Additional security settings
    CORS_ALLOWED_CREDENTIALS: bool = Field(
        default=False,
        description="Allow CORS credentials submission"
    )
    CORS_ALLOWED_METHODS: List[str] = Field(
        default=["GET", "POST", "PUT", "DELETE"],
        description="Allowed HTTP methods for CORS"
    )
    CORS_ALLOWED_HEADERS: List[str] = Field(
        default=["*"],
        description="Allowed headers for CORS"
    )

    # Model settings
    DEFAULT_MODEL_PROVIDER: str = Field(
        default="nanogpt",
        description="Default AI model provider"
    )
    
    # MFA settings (Authy)
    AUTHY_API_KEY: str = Field(
        default="",
        description="Authy API key for 2FA (leave empty to disable)"
    )
    AUTHY_API_URL: str = Field(
        default="https://api.authy.com",
        description="Authy API base URL"
    )
    AUTHY_TIMEOUT: int = Field(
        default=10,
        description="Authy API timeout in seconds"
    )
    
    # Validation methods
    @validator("SECRET_KEY")
    def validate_secret(cls, v):
        return SecuritySettings.validate_secret_key(v)
    
    @validator("ALGORITHM")
    def validate_algorithm(cls, v):
        return SecuritySettings.validate_jwt_algorithm(v)
    
    @validator("DATABASE_URL")
    def validate_database(cls, v):
        return DatabaseSettings.validate_sqlite_url(v)
    
    @validator("REDIS_URL")
    def validate_redis(cls, v):
        return RedisSettings.validate_redis_url(v)
    
    @validator("PORT")
    def validate_port(cls, v):
        return APISettings.validate_port(v)
    
    @validator("HOST")
    def validate_host(cls, v):
        return APISettings.validate_host(v)
    
    @validator("ALLOWED_ORIGINS", pre=True)
    def validate_cors(cls, v):
        """Handle multiple formats: JSON array, [*], or comma-separated values."""
        if isinstance(v, str):
            v = v.strip()
            # Handle single value without brackets
            if v == "*":
                v = ["*"]
            # Handle bash-sourced format: [*]
            elif v == "[*]":
                v = ["*"]
            # Handle JSON array format
            elif v.startswith("[") and v.endswith("]"):
                try:
                    import json
                    v = json.loads(v)
                except json.JSONDecodeError:
                    # After bash sourcing: [http://localhost:5000, http://localhost:8000]
                    content = v[1:-1]  # Remove brackets
                    items = []
                    for item in content.split(","):
                        item = item.strip()
                        if item:
                            items.append(item)
                    v = items
            # Handle comma-separated without brackets
            elif "," in v:
                v = [item.strip() for item in v.split(",") if item.strip()]
        return APISettings.validate_cors_origins(v)
    
    @validator("CHROMA_PORT")
    def validate_chroma_port(cls, v):
        return APISettings.validate_port(v)
    
    @model_validator(mode="after")
    def validate_debug_mode(self):
        """Comprehensive debug mode security validation."""
        if self.DEBUG:
            warnings.warn("⚠️  Debug mode enabled - security warning triggered", RuntimeWarning)

            if len(self.SECRET_KEY) < 64:
                warnings.warn(
                    "Debug mode should use stronger secrets (>=64 chars)",
                    RuntimeWarning
                )

            if "*" in self.ALLOWED_ORIGINS:
                warnings.warn(
                    "Debug mode allows any CORS origin - use only for development",
                    RuntimeWarning
                )
        else:
            # Production mode: enforce stronger security requirements
            if len(self.SECRET_KEY) < 64:
                raise ConfigurationError(
                    "Production mode requires SECRET_KEY >= 64 characters. "
                    "Use a cryptographically random secret or set DEBUG=True for development."
                )
            if "*" in self.ALLOWED_ORIGINS:
                raise ConfigurationError(
                    "Production mode does not allow wildcard CORS origins. "
                    "Specify exact origins in ALLOWED_ORIGINS or set DEBUG=True for development."
                )

        return self
    
    def validate_all(self):
        """Perform comprehensive configuration validation."""
        try:
            # Validate core settings
            self.validate_secret(self.SECRET_KEY)
            self.validate_algorithm(self.ALGORITHM)
            self.validate_port(self.PORT)
            self.validate_host(self.HOST)
            self.validate_database(self.DATABASE_URL)
            self.validate_redis(self.REDIS_URL)
            self.validate_cors(self.ALLOWED_ORIGINS)
            
            # Additional deep validation
            self._validate_paths()
            self._validate_urls()
            
        except (ValidationError, ConfigurationError) as e:
            raise ConfigurationError(f"Configuration validation failed: {e}")
    
    def _validate_paths(self):
        """Validate file system paths."""
        paths_to_check = [
            self.CHROMA_PERSIST_DIR,
        ]
        
        for path_str in paths_to_check:
            if not path_str:
                continue
                
            path = Path(path_str)
            if not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
    
    def _validate_urls(self):
        """Validate URL format for all settings."""
        url_validations = [
            (self.DATABASE_URL, "database"),
            (self.REDIS_URL, "redis"),
        ]
        
        for url, service_type in url_validations:
            DatabaseSettings.validate_url_format(url, service_type)


# Import and setup
from pathlib import Path
import logging

# Create settings instance with validation
try:
    settings = Settings()
    logger = logging.getLogger(__name__)
    logger.info("✅ Configuration validation successful")
except ValidationError as e:
    logger = logging.getLogger(__name__)
    logger.critical(f"Configuration validation error: {e}")
    raise
except ConfigurationError as e:
    logger = logging.getLogger(__name__)
    logger.critical(f"Configuration error: {e}")
    raise
