"""
API Key management for image serving endpoints.

Provides secure API key generation, storage, and validation for
authenticating display devices and external integrations.
"""

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import yaml

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.utils.logging import logger

# Argon2id hasher (includes automatic salt)
_hasher = PasswordHasher(
    time_cost=2,        # iterations
    memory_cost=65536,  # 64 MB
    parallelism=1,
)


@dataclass
class APIKey:
    """Represents an API key with metadata."""
    id: str
    name: str
    key_hash: str
    key_prefix: str
    created_at: datetime
    last_used: Optional[datetime] = None
    scopes: List[str] = field(default_factory=lambda: ["images:read"])
    enabled: bool = True

    def to_dict(self, include_hash: bool = False) -> dict:
        """Convert to dictionary for API responses."""
        result = {
            "id": self.id,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "scopes": self.scopes,
            "enabled": self.enabled,
        }
        if include_hash:
            result["key_hash"] = self.key_hash
        return result

    def to_config_dict(self) -> dict:
        """Convert to dictionary for YAML config storage."""
        return {
            "id": self.id,
            "name": self.name,
            "key_hash": self.key_hash,
            "key_prefix": self.key_prefix,
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "scopes": self.scopes,
            "enabled": self.enabled,
        }

    @classmethod
    def from_config_dict(cls, data: dict) -> "APIKey":
        """Create APIKey from YAML config dictionary."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now()

        last_used = data.get("last_used")
        if isinstance(last_used, str):
            last_used = datetime.fromisoformat(last_used)

        return cls(
            id=data["id"],
            name=data.get("name", "Unnamed Key"),
            key_hash=data["key_hash"],
            key_prefix=data.get("key_prefix", "sk_****"),
            created_at=created_at,
            last_used=last_used,
            scopes=data.get("scopes", ["images:read"]),
            enabled=data.get("enabled", True),
        )


class APIKeyManager:
    """Manages API keys for authentication."""

    def __init__(self, config_path: Path = Path("config/api_keys.yaml")):
        self.config_path = config_path
        self.keys: Dict[str, APIKey] = {}
        self._dirty = False  # Track if save is needed

    def generate_key(self) -> tuple[str, str, str]:
        """
        Generate a new API key with Argon2id hash.

        Returns:
            Tuple of (full_key, key_hash, key_prefix)
        """
        # Generate 32 character hex key with sk_ prefix
        key = "sk_" + secrets.token_hex(16)
        # Argon2 includes salt automatically in the hash string
        key_hash = _hasher.hash(key)
        # Show first 4 and last 4 chars of the random part
        key_prefix = f"sk_{key[3:7]}...{key[-4:]}"
        return key, key_hash, key_prefix

    def verify_key(self, key: str) -> Optional[APIKey]:
        """
        Verify an API key against stored Argon2id hash.

        Args:
            key: The full API key to verify

        Returns:
            APIKey object if valid and enabled, None otherwise
        """
        if not key or not key.startswith("sk_"):
            return None

        for api_key in self.keys.values():
            if not api_key.enabled:
                continue
            try:
                # Argon2 verify handles salt extraction from stored hash
                _hasher.verify(api_key.key_hash, key)
                # Update last_used timestamp
                api_key.last_used = datetime.now()
                self._dirty = True
                return api_key
            except VerifyMismatchError:
                continue

        return None

    def create_key(self, name: str, scopes: Optional[List[str]] = None) -> tuple[str, APIKey]:
        """
        Create a new API key.

        Args:
            name: Human-readable name for the key
            scopes: List of permission scopes (default: ["images:read"])

        Returns:
            Tuple of (full_key, APIKey object)
            Note: full_key is only returned once and should be shown to user
        """
        if scopes is None:
            scopes = ["images:read"]

        # Generate unique ID
        key_id = secrets.token_hex(8)
        while key_id in self.keys:
            key_id = secrets.token_hex(8)

        # Generate the key
        full_key, key_hash, key_prefix = self.generate_key()

        # Create APIKey object
        api_key = APIKey(
            id=key_id,
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            created_at=datetime.now(),
            scopes=scopes,
            enabled=True,
        )

        self.keys[key_id] = api_key
        self._dirty = True

        logger.info(f"Created API key '{name}' (id: {key_id})")
        return full_key, api_key

    def revoke_key(self, key_id: str) -> bool:
        """
        Revoke (delete) an API key.

        Args:
            key_id: The key ID to revoke

        Returns:
            True if key was found and deleted, False otherwise
        """
        if key_id in self.keys:
            key_name = self.keys[key_id].name
            del self.keys[key_id]
            self._dirty = True
            logger.info(f"Revoked API key '{key_name}' (id: {key_id})")
            return True
        return False

    def update_key(
        self,
        key_id: str,
        name: Optional[str] = None,
        enabled: Optional[bool] = None,
        scopes: Optional[List[str]] = None,
    ) -> Optional[APIKey]:
        """
        Update API key metadata.

        Args:
            key_id: The key ID to update
            name: New name (optional)
            enabled: New enabled status (optional)
            scopes: New scopes (optional)

        Returns:
            Updated APIKey object or None if not found
        """
        if key_id not in self.keys:
            return None

        api_key = self.keys[key_id]

        if name is not None:
            api_key.name = name
        if enabled is not None:
            api_key.enabled = enabled
        if scopes is not None:
            api_key.scopes = scopes

        self._dirty = True
        logger.info(f"Updated API key '{api_key.name}' (id: {key_id})")
        return api_key

    def get_key(self, key_id: str) -> Optional[APIKey]:
        """Get an API key by ID."""
        return self.keys.get(key_id)

    def list_keys(self) -> List[APIKey]:
        """List all API keys."""
        return list(self.keys.values())

    async def load_config(self) -> int:
        """
        Load API keys from YAML file.

        Returns:
            Number of keys loaded
        """
        if not self.config_path.exists():
            logger.info(f"No API keys config found at {self.config_path}")
            return 0

        try:
            with open(self.config_path, "r") as f:
                config = yaml.safe_load(f) or {}

            api_keys_data = config.get("api_keys", [])
            if not isinstance(api_keys_data, list):
                api_keys_data = []

            self.keys.clear()
            for key_data in api_keys_data:
                try:
                    api_key = APIKey.from_config_dict(key_data)
                    self.keys[api_key.id] = api_key
                except Exception as e:
                    logger.error(f"Failed to load API key: {e}")

            logger.info(f"Loaded {len(self.keys)} API keys from {self.config_path}")
            self._dirty = False
            return len(self.keys)

        except Exception as e:
            logger.error(f"Failed to load API keys config: {e}")
            return 0

    async def save_config(self) -> None:
        """Save API keys to YAML file."""
        try:
            # Ensure config directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            config = {
                "api_keys": [key.to_config_dict() for key in self.keys.values()]
            }

            with open(self.config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

            self._dirty = False
            logger.info(f"Saved {len(self.keys)} API keys to {self.config_path}")

        except Exception as e:
            logger.error(f"Failed to save API keys config: {e}")
            raise

    async def save_if_dirty(self) -> None:
        """Save config only if there are unsaved changes."""
        if self._dirty:
            await self.save_config()
