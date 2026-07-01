"""
API Key management for image serving endpoints.

Provides secure API key generation, storage, and validation for
authenticating display devices and external integrations.

Keys are persisted in SQLite (``config/api_keys.db``), mirroring
``CurationService``: per-key write-through, indexed lookups, WAL. Each key is
verified by its Argon2id hash (one-way), and additionally stores the full
``sk_…`` secret Fernet-encrypted (``enc_secret``) so the admin dashboard can
reveal it again and build authorized display URLs. See ``app.auth.key_crypto``.
"""

import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.auth.key_crypto import decrypt_secret, encrypt_secret
from app.utils.logging import logger

# Argon2id hasher (includes automatic salt)
_hasher = PasswordHasher(
    time_cost=2,        # iterations
    memory_cost=65536,  # 64 MB
    parallelism=1,
)


# Length of the non-secret lookup prefix taken from the start of a key.
# "sk_" + 9 hex chars = 36 bits, enough to disambiguate candidate keys for an
# O(1) lookup while leaving the bulk of the key's entropy (still ~92 bits)
# unexposed. Used only to avoid an O(n) Argon2 scan per authentication.
KEY_LOOKUP_LEN = 12


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
    # Non-secret prefix of the key for fast candidate lookup ("" for legacy
    # keys created before this field existed — those use the linear fallback).
    lookup_prefix: str = ""
    # Fernet-encrypted full secret, or None for keys whose secret was never
    # captured (migrated from the legacy hash-only YAML store). When present,
    # the key is "recoverable" — the dashboard can reveal it / build URLs.
    encrypted_key: Optional[str] = None

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
            # Whether the full secret can be recovered (never the ciphertext).
            "recoverable": self.encrypted_key is not None,
        }
        if include_hash:
            result["key_hash"] = self.key_hash
        return result


class APIKeyManager:
    """Manages API keys for authentication, backed by SQLite."""

    def __init__(self, db_path: Path = Path("config/api_keys.db")):
        self.db_path = Path(db_path)
        self.keys: Dict[str, APIKey] = {}
        # lookup_prefix -> list of key ids (fast candidate lookup for verify)
        self._lookup_index: Dict[str, List[str]] = {}
        # key ids whose in-memory last_used is ahead of the DB (flushed lazily
        # so the hot verify path doesn't write on every authentication).
        self._dirty_last_used: set[str] = set()
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------ #
    # SQLite setup
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    key_prefix TEXT NOT NULL,
                    lookup_prefix TEXT NOT NULL DEFAULT '',
                    enc_secret TEXT,
                    scopes TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_used TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_apikeys_lookup ON api_keys(lookup_prefix);
                CREATE INDEX IF NOT EXISTS idx_apikeys_enabled ON api_keys(enabled);
                """
            )
        logger.info(f"API keys DB ready at {self.db_path}")

    def _row_to_key(self, row: tuple) -> APIKey:
        (
            id_, name, key_hash, key_prefix, lookup_prefix, enc_secret,
            scopes_json, enabled, created_at, last_used,
        ) = row
        try:
            scopes = json.loads(scopes_json) if scopes_json else ["images:read"]
        except (ValueError, TypeError):
            scopes = ["images:read"]
        return APIKey(
            id=id_,
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            created_at=datetime.fromisoformat(created_at) if created_at else datetime.now(),
            last_used=datetime.fromisoformat(last_used) if last_used else None,
            scopes=scopes,
            enabled=bool(enabled),
            lookup_prefix=lookup_prefix or "",
            encrypted_key=enc_secret,
        )

    def _rebuild_index(self) -> None:
        """Rebuild the lookup_prefix -> key ids index from self.keys."""
        self._lookup_index = {}
        for key_id, api_key in self.keys.items():
            if api_key.lookup_prefix:
                self._lookup_index.setdefault(api_key.lookup_prefix, []).append(key_id)

    def _upsert_row(self, conn: sqlite3.Connection, api_key: APIKey) -> None:
        conn.execute(
            """
            INSERT INTO api_keys
                (id, name, key_hash, key_prefix, lookup_prefix, enc_secret,
                 scopes, enabled, created_at, last_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                key_hash=excluded.key_hash,
                key_prefix=excluded.key_prefix,
                lookup_prefix=excluded.lookup_prefix,
                enc_secret=excluded.enc_secret,
                scopes=excluded.scopes,
                enabled=excluded.enabled,
                last_used=excluded.last_used
            """,
            (
                api_key.id,
                api_key.name,
                api_key.key_hash,
                api_key.key_prefix,
                api_key.lookup_prefix,
                api_key.encrypted_key,
                json.dumps(api_key.scopes),
                int(api_key.enabled),
                api_key.created_at.isoformat(),
                api_key.last_used.isoformat() if api_key.last_used else None,
            ),
        )

    def _persist(self, api_key: APIKey) -> None:
        """Write-through a single key to SQLite."""
        with self._lock, self._connect() as conn:
            self._upsert_row(conn, api_key)

    # ------------------------------------------------------------------ #
    # key generation / verification
    # ------------------------------------------------------------------ #

    def generate_key(self) -> tuple[str, str, str, str]:
        """
        Generate a new API key with Argon2id hash.

        Returns:
            Tuple of (full_key, key_hash, key_prefix, lookup_prefix)
        """
        # Generate 32 character hex key with sk_ prefix
        key = "sk_" + secrets.token_hex(16)
        # Argon2 includes salt automatically in the hash string
        key_hash = _hasher.hash(key)
        # Show first 4 and last 4 chars of the random part
        key_prefix = f"sk_{key[3:7]}...{key[-4:]}"
        lookup_prefix = key[:KEY_LOOKUP_LEN]
        return key, key_hash, key_prefix, lookup_prefix

    def verify_key(self, key: str) -> Optional[APIKey]:
        """
        Verify an API key against stored Argon2id hash.

        Uses a non-secret lookup prefix to test only the matching candidate
        key(s) instead of running Argon2 against every stored key. Legacy keys
        without a lookup prefix fall back to a linear scan.

        Args:
            key: The full API key to verify

        Returns:
            APIKey object if valid and enabled, None otherwise
        """
        if not key or not key.startswith("sk_"):
            return None

        def _try(api_key: APIKey) -> bool:
            if not api_key.enabled:
                return False
            try:
                _hasher.verify(api_key.key_hash, key)
                return True
            except VerifyMismatchError:
                return False

        def _mark_used(api_key: APIKey) -> None:
            api_key.last_used = datetime.now()
            # verify_key may run in a thread-pool executor (hot auth path), so
            # guard the shared dirty-set against concurrent save_if_dirty().
            with self._lock:
                self._dirty_last_used.add(api_key.id)

        # Fast path: candidates sharing this key's lookup prefix
        candidate_ids = self._lookup_index.get(key[:KEY_LOOKUP_LEN], [])
        for key_id in candidate_ids:
            api_key = self.keys.get(key_id)
            if api_key and _try(api_key):
                _mark_used(api_key)
                return api_key

        # Fallback: legacy keys with no stored lookup prefix
        for api_key in self.keys.values():
            if api_key.lookup_prefix:
                continue  # already covered by the index
            if _try(api_key):
                _mark_used(api_key)
                return api_key

        return None

    # ------------------------------------------------------------------ #
    # mutations (write-through)
    # ------------------------------------------------------------------ #

    def create_key(self, name: str, scopes: Optional[List[str]] = None) -> tuple[str, APIKey]:
        """
        Create a new API key.

        Returns:
            Tuple of (full_key, APIKey object). full_key is only returned once;
            it is also stored encrypted so it can be revealed again later.
        """
        if scopes is None:
            scopes = ["images:read"]

        # Generate unique ID
        key_id = secrets.token_hex(8)
        while key_id in self.keys:
            key_id = secrets.token_hex(8)

        full_key, key_hash, key_prefix, lookup_prefix = self.generate_key()

        api_key = APIKey(
            id=key_id,
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            created_at=datetime.now(),
            scopes=scopes,
            enabled=True,
            lookup_prefix=lookup_prefix,
            encrypted_key=encrypt_secret(full_key),
        )

        self.keys[key_id] = api_key
        if lookup_prefix:
            self._lookup_index.setdefault(lookup_prefix, []).append(key_id)
        self._persist(api_key)

        logger.info(f"Created API key '{name}' (id: {key_id})")
        return full_key, api_key

    def rotate_key(self, key_id: str) -> Optional[tuple[str, APIKey]]:
        """
        Rotate (regenerate the secret of) an existing key, keeping its id,
        name, scopes and enabled state. The previous secret stops working
        immediately.

        Returns:
            Tuple of (new_full_key, APIKey) or None if the key doesn't exist.
        """
        api_key = self.keys.get(key_id)
        if api_key is None:
            return None

        # Drop the old lookup-index entry before re-keying.
        if api_key.lookup_prefix:
            ids = self._lookup_index.get(api_key.lookup_prefix)
            if ids and key_id in ids:
                ids.remove(key_id)
                if not ids:
                    del self._lookup_index[api_key.lookup_prefix]

        full_key, key_hash, key_prefix, lookup_prefix = self.generate_key()
        api_key.key_hash = key_hash
        api_key.key_prefix = key_prefix
        api_key.lookup_prefix = lookup_prefix
        api_key.encrypted_key = encrypt_secret(full_key)
        api_key.last_used = None

        if lookup_prefix:
            self._lookup_index.setdefault(lookup_prefix, []).append(key_id)
        self._dirty_last_used.discard(key_id)
        self._persist(api_key)

        logger.info(f"Rotated API key '{api_key.name}' (id: {key_id})")
        return full_key, api_key

    def reveal_key(self, key_id: str) -> Optional[str]:
        """Return the plaintext secret for a key, or None if unrecoverable."""
        api_key = self.keys.get(key_id)
        if api_key is None:
            return None
        return decrypt_secret(api_key.encrypted_key)

    def revoke_key(self, key_id: str) -> bool:
        """Revoke (delete) an API key. Returns True if it existed."""
        api_key = self.keys.get(key_id)
        if api_key is None:
            return False

        key_name = api_key.name
        if api_key.lookup_prefix:
            ids = self._lookup_index.get(api_key.lookup_prefix)
            if ids and key_id in ids:
                ids.remove(key_id)
                if not ids:
                    del self._lookup_index[api_key.lookup_prefix]
        del self.keys[key_id]
        self._dirty_last_used.discard(key_id)
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        logger.info(f"Revoked API key '{key_name}' (id: {key_id})")
        return True

    def update_key(
        self,
        key_id: str,
        name: Optional[str] = None,
        enabled: Optional[bool] = None,
        scopes: Optional[List[str]] = None,
    ) -> Optional[APIKey]:
        """Update API key metadata. Returns the updated key or None."""
        api_key = self.keys.get(key_id)
        if api_key is None:
            return None

        if name is not None:
            api_key.name = name
        if enabled is not None:
            api_key.enabled = enabled
        if scopes is not None:
            api_key.scopes = scopes

        self._persist(api_key)
        logger.info(f"Updated API key '{api_key.name}' (id: {key_id})")
        return api_key

    def get_key(self, key_id: str) -> Optional[APIKey]:
        """Get an API key by ID."""
        return self.keys.get(key_id)

    def list_keys(self) -> List[APIKey]:
        """List all API keys."""
        return list(self.keys.values())

    # ------------------------------------------------------------------ #
    # load / flush
    # ------------------------------------------------------------------ #

    async def load_config(self) -> int:
        """Load all API keys from SQLite into the in-memory cache."""
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, name, key_hash, key_prefix, lookup_prefix, enc_secret,
                           scopes, enabled, created_at, last_used
                    FROM api_keys
                    """
                ).fetchall()
            self.keys = {}
            for row in rows:
                try:
                    api_key = self._row_to_key(row)
                    self.keys[api_key.id] = api_key
                except Exception as e:
                    logger.error(f"Failed to load API key: {e}")
            self._rebuild_index()
            self._dirty_last_used.clear()
            logger.info(f"Loaded {len(self.keys)} API keys from {self.db_path}")
            return len(self.keys)
        except Exception as e:
            logger.error(f"Failed to load API keys: {e}")
            return 0

    async def save_config(self) -> None:
        """Flush any pending state. Mutations already write through, so this
        only needs to persist lazily-tracked last_used timestamps. Retained so
        existing call sites (post create/update/revoke) keep working."""
        await self.save_if_dirty()

    async def save_if_dirty(self) -> None:
        """Persist last_used timestamps deferred from the hot verify path."""
        # Snapshot + clear atomically so a concurrent verify_key() add can't be
        # lost or raise "set changed size during iteration".
        with self._lock:
            if not self._dirty_last_used:
                return
            ids = list(self._dirty_last_used)
            self._dirty_last_used.clear()
        with self._lock, self._connect() as conn:
            for key_id in ids:
                api_key = self.keys.get(key_id)
                if api_key is None:
                    continue
                conn.execute(
                    "UPDATE api_keys SET last_used=? WHERE id=?",
                    (api_key.last_used.isoformat() if api_key.last_used else None, key_id),
                )
