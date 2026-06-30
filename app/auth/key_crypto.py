"""Reversible encryption-at-rest for API key secrets.

API keys are verified by their Argon2id hash (one-way), but we also keep the
full ``sk_…`` secret so the admin dashboard can rebuild authorized display URLs
and surface a key again after creation. That secret is stored Fernet-encrypted
in the ``api_keys`` table; this module owns the symmetric key.

The Fernet key is *derived* (HKDF-SHA256) from a stable secret resolved in this
order:

  1. ``API_KEY_ENCRYPTION_SECRET`` env var (explicit operator control)
  2. ``SESSION_SECRET`` env var (already used for session cookies)
  3. a random secret auto-created at ``config/secret.key`` (0600) on first use

Because the key is derived, the on-disk ciphertext is only readable by a process
that can resolve the same secret. Rotating that secret invalidates existing
ciphertext — ``decrypt_secret`` returns ``None`` rather than raising, and
``scripts/reencrypt_api_keys.py`` covers the re-encrypt path.
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.utils.logging import logger

# Fixed, non-secret salt/info for the HKDF derivation. These don't need to be
# secret (HKDF's security comes from the input key material); they only need to
# be stable so the same secret always derives the same Fernet key.
_HKDF_SALT = b"screensaver-api-key-encryption-v1"
_HKDF_INFO = b"api-key-encryption"

_SECRET_FILE = Path("config/secret.key")

# Cached derived Fernet so we don't re-run HKDF on every encrypt/decrypt.
_fernet: Optional[Fernet] = None


def _resolve_secret() -> bytes:
    """Resolve the stable input secret used to derive the Fernet key."""
    for env_var in ("API_KEY_ENCRYPTION_SECRET", "SESSION_SECRET"):
        val = os.environ.get(env_var)
        if val:
            return val.encode("utf-8")

    # Fall back to a persisted random secret so encryption works out of the box
    # (e.g. AUTH_ENABLED=false, no OIDC) and stays stable across restarts.
    if _SECRET_FILE.exists():
        data = _SECRET_FILE.read_bytes().strip()
        if data:
            return data

    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    new_secret = secrets.token_hex(32).encode("utf-8")
    # Write before chmod so the secret never briefly exists world-readable.
    _SECRET_FILE.write_bytes(new_secret)
    try:
        os.chmod(_SECRET_FILE, 0o600)
    except OSError:
        # Best effort (e.g. non-POSIX filesystem) — the value is still derived.
        pass
    logger.info(f"Generated API key encryption secret at {_SECRET_FILE}")
    return new_secret


def _derive_fernet(secret: bytes) -> Fernet:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    derived = hkdf.derive(secret)
    return Fernet(base64.urlsafe_b64encode(derived))


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _derive_fernet(_resolve_secret())
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """Encrypt an API key secret for at-rest storage; returns a token string."""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: Optional[str]) -> Optional[str]:
    """Decrypt a stored token back to the secret, or ``None`` if unavailable.

    Returns ``None`` for an empty token (legacy/migrated keys with no stored
    secret) or when the token can't be decrypted with the current key (e.g. the
    derivation secret was rotated).
    """
    if not token:
        return None
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
