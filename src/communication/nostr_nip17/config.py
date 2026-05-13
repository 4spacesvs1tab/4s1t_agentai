"""
Configuration management for Nostr NIP-17 client

Supports up to 5 relays in various formats:
- relay.damus.io
- wss://relay.damus.io
- ws://your-local-relay:3356
- https://hixb2r4g54stav5iuz2wu2v6233z2lvbtptg2hs7avp3z2pkd4ejqeid.local
"""
import os
import yaml
import json
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .exceptions import ConfigurationError
from .nostr_client import RelayConfig

from utils.logger import setup_logger
logger = setup_logger(__name__)


@dataclass
class NIP17Config:
    """Configuration for NIP-17 Nostr client."""
    relays: List[RelayConfig] = field(default_factory=list)
    private_key: Optional[str] = None
    recipient_npub: Optional[str] = None
    enabled: bool = True
    auto_connect: bool = True
    connection_timeout: int = 15
    message_timeout: int = 15
    
    def __post_init__(self):
        if len(self.relays) > 5:
            raise ConfigurationError(f"Maximum 5 relays allowed, got {len(self.relays)}")


class NIP17ConfigManager:
    """
    Manages NIP-17 configuration from YAML files.
    
    Expected YAML format:
    nostr_nip17:
      enabled: true
      private_key: nsec1...  # or file reference: file:/path/to/key
      recipient_npub: npub1...
      relays:
        - url: wss://relay.damus.io
          priority: 1
          enabled: true
        - url: ws://your-local-relay:3356
          priority: 2
        - url: https://hixb2r4g54stav5iuz2wu2v6233z2lvbtptg2hs7avp3z2pkd4ejqeid.local
          priority: 3
      timeouts:
        connection: 15
        message: 15
    """
    
    DEFAULT_CONFIG_PATH = "config/nostr_nip17.yaml"
    SECRETS_PATH = ".secrets/nostr_nip17.json"
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._config: Optional[NIP17Config] = None
    
    def load_config(self) -> NIP17Config:
        """Load configuration from YAML file."""
        if not os.path.exists(self.config_path):
            raise ConfigurationError(f"Config file not found: {self.config_path}")
        
        try:
            with open(self.config_path, 'r') as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML: {e}")
        except Exception as e:
            raise ConfigurationError(f"Error reading config: {e}")
        
        if 'nostr_nip17' not in data:
            raise ConfigurationError("Missing 'nostr_nip17' section in config")
        
        nip17_data = data['nostr_nip17']
        
        # Parse relays
        relays = []
        relay_data = nip17_data.get('relays', [])
        
        if len(relay_data) > 5:
            raise ConfigurationError(f"Maximum 5 relays allowed, got {len(relay_data)}")
        
        for i, r in enumerate(relay_data):
            if isinstance(r, str):
                # Simple URL format
                relays.append(RelayConfig(url=r, priority=i+1))
            elif isinstance(r, dict):
                # Full config format
                relays.append(RelayConfig(
                    url=r['url'],
                    priority=r.get('priority', i+1),
                    enabled=r.get('enabled', True),
                    timeout=r.get('timeout', 15),
                    reconnect=r.get('reconnect', True)
                ))
        
        # Get private key (direct or from file)
        private_key = nip17_data.get('private_key', '')
        if private_key.startswith('file:'):
            key_file = private_key[5:]
            private_key = self._load_key_from_file(key_file)
        elif not private_key:
            # Try loading from secrets
            private_key = self._load_from_secrets('private_key')
        
        # Get recipient npub
        recipient_npub = nip17_data.get('recipient_npub', '')
        if not recipient_npub:
            recipient_npub = self._load_from_secrets('recipient_npub')
        
        # Timeouts
        timeouts = nip17_data.get('timeouts', {})
        
        self._config = NIP17Config(
            relays=relays,
            private_key=private_key,
            recipient_npub=recipient_npub,
            enabled=nip17_data.get('enabled', True),
            auto_connect=nip17_data.get('auto_connect', True),
            connection_timeout=timeouts.get('connection', 15),
            message_timeout=timeouts.get('message', 15)
        )
        
        return self._config
    
    def _load_key_from_file(self, key_file: str) -> str:
        """Load private key from file, falling back to env vars if file is absent."""
        if os.path.exists(key_file):
            try:
                with open(key_file, 'r') as f:
                    return f.read().strip()
            except Exception as e:
                raise ConfigurationError(f"Error reading key file: {e}")

        # File not present — try environment variables
        for env_var in ("NOSTR_NSEC", "APPROVAL_PRIVATE_KEY"):
            key = os.getenv(env_var, "")
            if key.startswith("nsec1"):
                logger.info(f"NIP-17: loaded private key from env var {env_var}")
                return key

        raise ConfigurationError(f"Key file not found: {key_file}")
    
    def _load_from_secrets(self, key: str) -> Optional[str]:
        """Load value from secrets file."""
        if not os.path.exists(self.SECRETS_PATH):
            return None
        
        try:
            with open(self.SECRETS_PATH, 'r') as f:
                secrets = json.load(f)
                return secrets.get(key)
        except:
            return None
    
    def save_secrets(self, private_key: str, recipient_npub: str) -> None:
        """Save secrets to secure file."""
        os.makedirs(os.path.dirname(self.SECRETS_PATH), exist_ok=True)
        
        secrets = {
            'private_key': private_key,
            'recipient_npub': recipient_npub
        }
        
        with open(self.SECRETS_PATH, 'w') as f:
            json.dump(secrets, f)
        
        # Set restrictive permissions
        os.chmod(self.SECRETS_PATH, 0o600)
    
    def create_example_config(self, path: Optional[str] = None) -> str:
        """Create example configuration file."""
        example = """# Nostr NIP-17 Configuration for 4S1T Agent AI
# Supports up to 5 relays

nostr_nip17:
  enabled: true
  
  # Private key - nsec1... format
  # Can be: direct string, file:/path/to/key, or omitted (loads from .secrets/nostr_nip17.json)
  private_key: nsec1...
  
  # Recipient npub - npub1... format
  recipient_npub: npub1...
  
  # Relay configuration (max 5 relays)
  # Supports various formats:
  #   - relay.damus.io (defaults to wss://)
  #   - wss://relay.damus.io
  #   - ws://your-local-relay:3356
  #   - https://hixb2r4g54stav5iuz2wu2v6233z2lvbtptg2hs7avp3z2pkd4ejqeid.local
  relays:
    # Primary relay
    - url: wss://relay.damus.io
      priority: 1
      enabled: true
      timeout: 15
      reconnect: true
    
    # Local relay example
    - url: ws://your-local-relay:3356
      priority: 2
    
    # .local domain example
    - url: wss://hixb2r4g54stav5iuz2wu2v6233z2lvbtptg2hs7avp3z2pkd4ejqeid.local
      priority: 3
    
    # Simple URL format (minimal config)
    - wss://relay.primal.net
    - wss://nos.lol
  
  # Timeouts in seconds
  timeouts:
    connection: 15
    message: 15
  
  # Auto-connect on client creation
  auto_connect: true
"""
        
        save_path = path or self.config_path
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        
        with open(save_path, 'w') as f:
            f.write(example)
        
        return save_path
    
    def validate_config(self, config: Optional[NIP17Config] = None) -> List[str]:
        """Validate configuration and return list of issues."""
        if config is None:
            config = self._config
        
        if config is None:
            return ["No configuration loaded"]
        
        issues = []
        
        if not config.enabled:
            issues.append("NIP-17 is disabled in configuration")
        
        if len(config.relays) == 0:
            issues.append("No relays configured")
        elif len(config.relays) > 5:
            issues.append(f"Too many relays: {len(config.relays)} (max 5)")
        
        if not config.private_key:
            issues.append("Private key not configured")
        elif not config.private_key.startswith('nsec1'):
            issues.append("Private key must start with 'nsec1'")
        
        if not config.recipient_npub:
            issues.append("Recipient npub not configured")
        elif not config.recipient_npub.startswith('npub1'):
            issues.append("Recipient npub must start with 'npub1'")
        
        return issues


# Helper function to load config from environment
def load_config_from_env() -> NIP17Config:
    """Load configuration from environment variables."""
    relays = []
    
    # Support NOSTR_RELAY_1 through NOSTR_RELAY_5
    for i in range(1, 6):
        relay_url = os.getenv(f'NOSTR_RELAY_{i}')
        if relay_url:
            relays.append(RelayConfig(url=relay_url, priority=i))
    
    private_key = os.getenv('NOSTR_NSEC')
    recipient_npub = os.getenv('NOSTR_RECIPIENT_NPUB')
    
    if not private_key:
        raise ConfigurationError("NOSTR_NSEC environment variable not set")
    if not recipient_npub:
        raise ConfigurationError("NOSTR_RECIPIENT_NPUB environment variable not set")
    if len(relays) == 0:
        raise ConfigurationError("At least one NOSTR_RELAY_X environment variable required")
    
    return NIP17Config(
        relays=relays,
        private_key=private_key,
        recipient_npub=recipient_npub
    )
