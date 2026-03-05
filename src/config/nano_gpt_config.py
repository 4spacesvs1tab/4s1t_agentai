"""
Nano-GPT.com configuration for the 4S1T Agent AI system.

This module provides configuration management for nano-gpt.com integration,
including API keys, model settings, and subscription information.
"""

import os
import json
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class NanoGPTConfig:
    """Configuration for nano-gpt.com integration."""
    
    # API Configuration
    api_key: str = ""
    base_url: str = "https://nano-gpt.com/api"
    api_endpoint: str = "/v1/chat/completions"
    
    # Subscription Information
    subscription_tier: str = "FREE"  # FREE or PRO
    subscription_features: Dict[str, Any] = field(default_factory=lambda: {
        "all_open_source_models": False,
        "no_additional_cost": False,
        "model_limit": "limited"
    })
    
    # Default Models by Task Type
    default_models: Dict[str, str] = field(default_factory=lambda: {
        "business_analysis": "glm-4.5",
        "data_analysis": "deepseek-v3.2",
        "quick_responses": "kimi-k2-0905",
        "reasoning": "glm-4.5",
        "coding": "deepseek-v3.2",
        "general": "deepseek-v3.2"
    })
    
    # Model Whitelist/Blacklist
    whitelist_models: list = field(default_factory=list)
    blacklist_models: list = field(default_factory=list)
    
    # Rate Limiting
    rate_limit_requests_per_minute: int = 60
    rate_limit_enabled: bool = True
    
    # Retry Configuration
    max_retry_attempts: int = 3
    retry_backoff_factor: float = 2.0
    
    # Security
    encrypt_api_key: bool = True
    config_file_path: str = "~/.4s1t/nano_gpt_config.json"
    
    def __post_init__(self):
        """Initialize configuration with environment variables or defaults."""
        # Override with environment variables if available
        self.api_key = os.getenv("NANO_GPT_API_KEY", self.api_key)
        self.subscription_tier = os.getenv("NANO_GPT_SUBSCRIPTION_TIER", self.subscription_tier)
        self.api_endpoint = os.getenv("NANO_GPT_API_ENDPOINT", self.api_endpoint)
        
        # Update subscription features based on tier
        if self.subscription_tier.upper() == "PRO":
            self.subscription_features = {
                "all_open_source_models": True,
                "no_additional_cost": True,
                "model_limit": "unlimited"
            }
    
    def is_pro_subscription(self) -> bool:
        """
        Check if PRO subscription is active.
        
        Returns:
            bool: True if PRO subscription, False otherwise
        """
        return self.subscription_tier.upper() == "PRO"
    
    def get_available_models(self) -> list:
        """
        Get list of available models based on subscription tier.
        
        Returns:
            list: List of available model names
        """
        if self.is_pro_subscription():
            # All PRO models
            return [
                "glm-4.6", "glm-4.5", "deepseek-r1",
                "deepseek-v3.2", "deepseek-v3.1",
                "kimi-k2-0905", "kimi-k2-0711",
                "qwen3-coder", "coding-specialists",
                "math-models", "venice", "roleplaying"
            ]
        else:
            # Free tier models (example)
            return ["glm-4.5", "deepseek-v3.2", "kimi-k2-0905"]
    
    def is_model_allowed(self, model_name: str) -> bool:
        """
        Check if a model is allowed based on whitelist/blacklist.
        
        Args:
            model_name: Name of the model to check
            
        Returns:
            bool: True if model is allowed, False otherwise
        """
        # Check whitelist
        if self.whitelist_models and model_name not in self.whitelist_models:
            return False
        
        # Check blacklist
        if model_name in self.blacklist_models:
            return False
        
        # Check subscription availability
        if self.is_pro_subscription():
            return True  # All models available with PRO
        else:
            return model_name in self.get_available_models()
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert configuration to dictionary.
        
        Returns:
            Dict[str, Any]: Configuration as dictionary
        """
        return {
            "api_key": self.api_key,
            "api_endpoint": self.api_endpoint,
            "subscription_tier": self.subscription_tier,
            "subscription_features": self.subscription_features,
            "default_models": self.default_models,
            "whitelist_models": self.whitelist_models,
            "blacklist_models": self.blacklist_models,
            "rate_limit_requests_per_minute": self.rate_limit_requests_per_minute,
            "rate_limit_enabled": self.rate_limit_enabled,
            "max_retry_attempts": self.max_retry_attempts,
            "retry_backoff_factor": self.retry_backoff_factor,
            "encrypt_api_key": self.encrypt_api_key
        }
    
    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'NanoGPTConfig':
        """
        Create configuration from dictionary.
        
        Args:
            config_dict: Configuration dictionary
            
        Returns:
            NanoGPTConfig: Configuration instance
        """
        config = cls()
        for key, value in config_dict.items():
            if hasattr(config, key):
                setattr(config, key, value)
        return config


class NanoGPTConfigManager:
    """Manager for nano-gpt.com configuration."""
    
    def __init__(self, config_file_path: Optional[str] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_file_path: Path to configuration file
        """
        self.config_file_path = config_file_path or os.path.expanduser("~/.4s1t/nano_gpt_config.json")
        self.config = NanoGPTConfig()
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def load_config(self) -> bool:
        """
        Load configuration from file.
        
        Returns:
            bool: True if loaded successfully, False otherwise
        """
        try:
            config_path = Path(self.config_file_path)
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config_data = json.load(f)
                    self.config = NanoGPTConfig.from_dict(config_data)
                    self.logger.info("Nano-GPT configuration loaded successfully")
                    return True
            else:
                self.logger.info("No existing nano-gpt configuration file found")
                return False
        except Exception as e:
            self.logger.error(f"Error loading nano-gpt configuration: {e}")
            return False
    
    def save_config(self) -> bool:
        """
        Save configuration to file.
        
        Returns:
            bool: True if saved successfully, False otherwise
        """
        try:
            config_path = Path(self.config_file_path)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(config_path, 'w') as f:
                json.dump(self.config.to_dict(), f, indent=2)
            
            self.logger.info("Nano-GPT configuration saved successfully")
            return True
        except Exception as e:
            self.logger.error(f"Error saving nano-gpt configuration: {e}")
            return False
    
    def get_config(self) -> NanoGPTConfig:
        """
        Get current configuration.
        
        Returns:
            NanoGPTConfig: Current configuration
        """
        return self.config
    
    def update_config(self, new_config: NanoGPTConfig) -> bool:
        """
        Update configuration.
        
        Args:
            new_config: New configuration
            
        Returns:
            bool: True if updated successfully, False otherwise
        """
        try:
            self.config = new_config
            self.logger.info("Nano-GPT configuration updated")
            return True
        except Exception as e:
            self.logger.error(f"Error updating nano-gpt configuration: {e}")
            return False


# Global configuration manager instance
nano_gpt_config_manager: Optional[NanoGPTConfigManager] = None


def get_nano_gpt_config_manager() -> NanoGPTConfigManager:
    """
    Get singleton nano-gpt configuration manager instance.
    
    Returns:
        NanoGPTConfigManager instance
    """
    global nano_gpt_config_manager
    if nano_gpt_config_manager is None:
        nano_gpt_config_manager = NanoGPTConfigManager()
    return nano_gpt_config_manager


def get_nano_gpt_config() -> NanoGPTConfig:
    """
    Get current nano-gpt configuration.
    
    Returns:
        NanoGPTConfig: Current configuration
    """
    manager = get_nano_gpt_config_manager()
    return manager.get_config()
