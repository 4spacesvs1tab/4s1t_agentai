"""
Startup configuration loader for the 4S1T Agent AI framework.

Loads and validates configuration from various sources including files, environment variables, and command line arguments.
"""
import os
import json
import yaml
from typing import Dict, Any, Optional, List
from pathlib import Path
import argparse

from config.settings import Settings
from utils.logger import setup_logger

logger = setup_logger(__name__)


class ConfigLoader:
    """Loads and manages application configuration from multiple sources."""
    
    def __init__(self, settings: Settings = None):
        """
        Initialize the configuration loader.
        
        Args:
            settings: Application settings instance
        """
        self.settings = settings or Settings()
        self._loaded_config: Dict[str, Any] = {}
        self._config_sources: List[str] = []
    
    def load_from_file(self, file_path: str, format: str = "auto") -> Dict[str, Any]:
        """
        Load configuration from a file.
        
        Args:
            file_path: Path to the configuration file
            format: Format of the file ("json", "yaml", "yml", or "auto")
            
        Returns:
            Loaded configuration dictionary
        """
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"Configuration file not found: {file_path}")
            return {}
        
        if format == "auto":
            suffix = path.suffix.lower()
            if suffix in [".yaml", ".yml"]:
                format = "yaml"
            elif suffix == ".json":
                format = "json"
            else:
                raise ValueError(f"Unsupported configuration file format: {suffix}")
        
        try:
            with open(path, 'r') as f:
                if format == "yaml":
                    config = yaml.safe_load(f)
                elif format == "json":
                    config = json.load(f)
                else:
                    raise ValueError(f"Unsupported format: {format}")
            
            if not isinstance(config, dict):
                raise ValueError("Configuration file must contain a dictionary")
            
            self._loaded_config.update(config)
            self._config_sources.append(f"file:{file_path}")
            logger.info(f"Loaded configuration from {file_path}")
            return config
        except Exception as e:
            logger.error(f"Failed to load configuration from {file_path}: {str(e)}")
            raise
    
    def load_from_env(self, prefix: str = "AGENT_") -> Dict[str, Any]:
        """
        Load configuration from environment variables.
        
        Args:
            prefix: Prefix to filter environment variables
            
        Returns:
            Loaded configuration dictionary
        """
        env_config = {}
        for key, value in os.environ.items():
            if key.startswith(prefix):
                config_key = key[len(prefix):].lower()
                # Try to convert to appropriate type
                try:
                    # Try integer
                    if value.isdigit():
                        env_config[config_key] = int(value)
                    # Try float
                    elif "." in value and all(c.isdigit() or c == "." for c in value):
                        env_config[config_key] = float(value)
                    # Try boolean
                    elif value.lower() in ["true", "false"]:
                        env_config[config_key] = value.lower() == "true"
                    # String
                    else:
                        env_config[config_key] = value
                except ValueError:
                    env_config[config_key] = value
        
        self._loaded_config.update(env_config)
        self._config_sources.append(f"env:{prefix}")
        logger.info(f"Loaded configuration from environment variables with prefix {prefix}")
        return env_config
    
    def load_from_args(self) -> Dict[str, Any]:
        """
        Load configuration from command line arguments.
        
        Returns:
            Loaded configuration dictionary
        """
        parser = argparse.ArgumentParser(description="4S1T Agent AI Configuration")
        
        # Add common configuration arguments
        parser.add_argument("--config-file", help="Path to configuration file")
        parser.add_argument("--host", help="Host to bind the server to")
        parser.add_argument("--port", type=int, help="Port to bind the server to")
        parser.add_argument("--debug", action="store_true", help="Enable debug mode")
        parser.add_argument("--log-level", help="Logging level")
        
        # Parse known args to avoid conflicts with other parsers
        args, _ = parser.parse_known_args()
        
        arg_config = {}
        if args.config_file:
            arg_config["config_file"] = args.config_file
        if args.host:
            arg_config["host"] = args.host
        if args.port:
            arg_config["port"] = args.port
        if args.debug:
            arg_config["debug"] = args.debug
        if args.log_level:
            arg_config["log_level"] = args.log_level
        
        self._loaded_config.update(arg_config)
        self._config_sources.append("args")
        logger.info("Loaded configuration from command line arguments")
        return arg_config
    
    def get_config_value(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key.
        
        Args:
            key: Configuration key
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        return self._loaded_config.get(key, default)
    
    def get_nested_config_value(self, key_path: str, default: Any = None) -> Any:
        """
        Get a nested configuration value using dot notation.
        
        Args:
            key_path: Dot-separated path to the configuration value (e.g., "database.host")
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key_path.split(".")
        current = self._loaded_config
        
        try:
            for key in keys:
                current = current[key]
            return current
        except (KeyError, TypeError):
            return default
    
    def get_all_config(self) -> Dict[str, Any]:
        """
        Get all loaded configuration.
        
        Returns:
            Complete configuration dictionary
        """
        return self._loaded_config.copy()
    
    def get_config_sources(self) -> List[str]:
        """
        Get the sources from which configuration was loaded.
        
        Returns:
            List of configuration sources
        """
        return self._config_sources.copy()
    
    def validate_config(self, required_keys: List[str] = None) -> bool:
        """
        Validate that all required configuration keys are present.
        
        Args:
            required_keys: List of required configuration keys
            
        Returns:
            True if all required keys are present, False otherwise
        """
        if required_keys is None:
            # Default required keys
            required_keys = ["host", "port"]
        
        missing_keys = []
        for key in required_keys:
            if self.get_config_value(key) is None:
                missing_keys.append(key)
        
        if missing_keys:
            logger.error(f"Missing required configuration keys: {missing_keys}")
            return False
        
        logger.info("Configuration validation passed")
        return True


# Convenience functions
def get_config_loader() -> ConfigLoader:
    """Get a configuration loader instance."""
    return ConfigLoader()


def load_startup_config() -> Dict[str, Any]:
    """
    Load startup configuration from all available sources.
    
    Returns:
        Loaded configuration dictionary
    """
    loader = ConfigLoader()
    
    # Load from command line arguments first
    loader.load_from_args()
    
    # Load from configuration file if specified
    config_file = loader.get_config_value("config_file")
    if config_file:
        loader.load_from_file(config_file)
    
    # Load from environment variables
    loader.load_from_env()
    
    return loader.get_all_config()
