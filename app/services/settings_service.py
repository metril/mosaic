"""Runtime-tunable application settings, backed by SQLite.

Only settings that genuinely apply at *use-time* live here (so a UI edit takes
effect without a restart). Boot-time, new-object-only, and security/mount
settings stay in environment variables.

Resolution per key: stored DB value → seeded env var → hardcoded default. On
first run each key is seeded from its env var (existing deployments keep
working); thereafter the admin Settings UI is the source of truth. The Home
Assistant token is stored Fernet-encrypted (see app.auth.key_crypto).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.auth.key_crypto import decrypt_secret, encrypt_secret
from app.utils.logging import logger

# key -> (type, env_var or None, default).  type ∈ {str, int, float, bool, secret}
SCHEMA: Dict[str, Tuple[str, Optional[str], Any]] = {
    # Home Assistant
    "ha_url": ("str", "HOMEASSISTANT_URL", ""),
    "ha_token": ("secret", "HOMEASSISTANT_TOKEN", ""),
    "ha_enabled": ("bool", None, False),
    "ha_http_timeout": ("float", "HA_HTTP_TIMEOUT", 10.0),
    "ha_weather_cache_ttl": ("int", "HA_WEATHER_CACHE_TTL", 600),
    # Immich
    "immich_http_timeout": ("float", "IMMICH_HTTP_TIMEOUT", 30.0),
    "max_immich_pull_count": ("int", "MAX_IMMICH_PULL_COUNT", 1000),
    # Display & connection limits
    "preload_lead_time": ("int", "PRELOAD_LEAD_TIME", 5),
    "browser_stale_timeout": ("int", "BROWSER_STALE_TIMEOUT", 60),
    "cleanup_interval": ("int", "CLEANUP_INTERVAL", 30),
    "endpoint_cleanup": ("str", "ENDPOINT_CLEANUP", "24h"),
    "max_browsers_per_group": ("int", "MAX_BROWSERS_PER_GROUP", 100),
    "max_groups": ("int", "MAX_GROUPS", 50),
}

_SECRET_KEYS = {k for k, (t, _, _) in SCHEMA.items() if t == "secret"}


def _truthy(s: str) -> bool:
    return s.strip().lower() in ("1", "true", "yes", "on")


class SettingsService:
    def __init__(self, db_path: Path = Path("config/settings.db")):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._cache: Dict[str, str] = {}  # key -> stored string (ciphertext for secrets)
        self._init_db()
        self._load()
        self._seed_from_env()

    # ---- storage ---------------------------------------------------------- #

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
            )
        logger.info(f"Settings store ready: {self.db_path}")

    def _load(self) -> None:
        with self._lock, self._connect() as conn:
            self._cache = {k: v for k, v in conn.execute("SELECT key, value FROM settings")}

    def _write(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        self._cache[key] = value

    def _seed_from_env(self) -> None:
        """Seed any unset managed key from its env var (one-time)."""
        for key, (typ, env_var, _default) in SCHEMA.items():
            if key in self._cache:
                continue
            if env_var:
                raw = os.environ.get(env_var)
                if raw is not None and raw != "":
                    self._write(key, encrypt_secret(raw) if typ == "secret" else str(raw))
        # ha_enabled has no env var: enable if URL+token were provided.
        if "ha_enabled" not in self._cache:
            if os.environ.get("HOMEASSISTANT_URL") and os.environ.get("HOMEASSISTANT_TOKEN"):
                self._write("ha_enabled", "true")

    # ---- typed access ----------------------------------------------------- #

    def _raw(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def get_str(self, key: str) -> str:
        v = self._raw(key)
        return v if v is not None else str(SCHEMA[key][2])

    def get_int(self, key: str) -> int:
        v = self._raw(key)
        try:
            return int(v) if v is not None else int(SCHEMA[key][2])
        except (ValueError, TypeError):
            logger.warning(f"Setting {key}={v!r} is not a valid int; using default {SCHEMA[key][2]}")
            return int(SCHEMA[key][2])

    def get_float(self, key: str) -> float:
        v = self._raw(key)
        try:
            return float(v) if v is not None else float(SCHEMA[key][2])
        except (ValueError, TypeError):
            logger.warning(f"Setting {key}={v!r} is not a valid float; using default {SCHEMA[key][2]}")
            return float(SCHEMA[key][2])

    def get_bool(self, key: str) -> bool:
        v = self._raw(key)
        return _truthy(v) if v is not None else bool(SCHEMA[key][2])

    def get_secret(self, key: str) -> str:
        """Decrypted plaintext of a secret key (empty string if unset)."""
        return decrypt_secret(self._raw(key)) or ""

    def has_secret(self, key: str) -> bool:
        return bool(self._raw(key))

    # ---- mutation --------------------------------------------------------- #

    def set(self, key: str, value: Any) -> None:
        if key not in SCHEMA:
            raise KeyError(f"Unknown setting: {key}")
        typ = SCHEMA[key][0]
        if typ == "secret":
            self._write(key, encrypt_secret(str(value)) if value else "")
        elif typ == "bool":
            self._write(key, "true" if (value if isinstance(value, bool) else _truthy(str(value))) else "false")
        else:
            self._write(key, str(value))

    def export_raw(self) -> Dict[str, str]:
        """Raw stored key→value pairs (ciphertext for secrets) for config backup."""
        return dict(self._cache)

    def import_raw(self, data: Dict[str, str]) -> None:
        """Replace all settings with a backup's raw rows, then reload the cache.
        Only known schema keys are accepted."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM settings")
            conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                [(k, str(v)) for k, v in data.items() if k in SCHEMA],
            )
        self._load()

    def ha_config(self) -> Dict[str, Any]:
        """Resolved Home Assistant config (token decrypted) for building the provider."""
        return {
            "url": self.get_str("ha_url"),
            "token": self.get_secret("ha_token"),
            "enabled": self.get_bool("ha_enabled"),
        }

    def to_public_dict(self) -> Dict[str, Any]:
        """All managed settings for the admin UI; secrets masked to has_* booleans."""
        out: Dict[str, Any] = {}
        for key, (typ, _env, _default) in SCHEMA.items():
            if typ == "secret":
                out[f"has_{key}"] = self.has_secret(key)
            elif typ == "int":
                out[key] = self.get_int(key)
            elif typ == "float":
                out[key] = self.get_float(key)
            elif typ == "bool":
                out[key] = self.get_bool(key)
            else:
                out[key] = self.get_str(key)
        return out
