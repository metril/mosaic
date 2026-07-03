#!/usr/bin/env python3
"""One-shot: re-encrypt at-rest secrets after the Screensaver→Mosaic rebrand.

The rebrand changed the Argon2id **salt** used to derive the Fernet key that
encrypts secrets at rest (app/auth/key_crypto.py: ``_KDF_SALT`` went from
``screensaver-api-key-encryption-v2`` → ``mosaic-api-key-encryption-v3``). The
input secret (``API_KEY_ENCRYPTION_SECRET`` / ``SESSION_SECRET`` /
``config/secret.key``) is unchanged — only the salt moved — so every stored
ciphertext must be decrypted with the OLD salt and re-encrypted with the NEW
one, or the app will silently treat those secrets as unset.

This is a standalone migration, intentionally NOT part of the app. Run it once,
with the app stopped, using the SAME encryption secret the app resolves (set
``SESSION_SECRET`` / ``API_KEY_ENCRYPTION_SECRET`` in your shell if the app gets
them from the environment, e.g. ``set -a; source .env; set +a``; otherwise it
falls back to ``{config-dir}/secret.key`` like the app does).

Covers all three secret stores under ``--config-dir``:
  * api_keys.db     → api_keys.enc_secret            (API-key secrets)
  * settings.db     → settings.value WHERE key='ha_token'  (Home Assistant token)
  * immich_pulls.db → pulls.config_json .api_key      (per-pull Immich keys)

Each value is decrypted with the old salt and re-encrypted with the new one.
Values that already decrypt under the NEW salt (a re-run) or under neither are
left untouched and reported — so the script is idempotent and safe to re-run.

    python3 scripts/migrate_rebrand_secrets.py --config-dir config [--dry-run]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sqlite3
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.fernet import Fernet, InvalidToken

# Self-contained by design (this is a one-shot migration, NOT part of the app):
# it depends only on argon2-cffi + cryptography, so it can run wherever the DBs
# and secret live without importing the FastAPI app.
#
# The salts before/after the rebrand, and the Argon2id work factors — these must
# match app/auth/key_crypto.py (_KDF_SALT + _KDF_TIME/MEMORY/PARALLELISM). If the
# app ever changes those, update here too.
OLD_SALT = b"screensaver-api-key-encryption-v2"
NEW_SALT = b"mosaic-api-key-encryption-v3"
KDF_TIME_COST = 3
KDF_MEMORY_COST = 65536  # KiB = 64 MiB
KDF_PARALLELISM = 1

# The only secret-typed key in the settings store (see settings_service.SCHEMA).
SECRET_SETTING_KEYS = ("ha_token",)


def _resolve_secret(config_dir: Path) -> bytes | None:
    """Resolve the derivation secret exactly as the app does (minus auto-create).

    Order: API_KEY_ENCRYPTION_SECRET → SESSION_SECRET → {config-dir}/secret.key.
    Returns None if none is available (the app would auto-create one, but for a
    migration that would just mint a useless fresh key).
    """
    for env_var in ("API_KEY_ENCRYPTION_SECRET", "SESSION_SECRET"):
        val = os.environ.get(env_var)
        if val:
            return val.encode("utf-8")
    secret_file = config_dir / "secret.key"
    if secret_file.exists():
        data = secret_file.read_bytes().strip()
        if data:
            return data
    return None


def _derive(secret: bytes, salt: bytes) -> Fernet:
    raw = hash_secret_raw(
        secret=secret,
        salt=salt,
        time_cost=KDF_TIME_COST,
        memory_cost=KDF_MEMORY_COST,
        parallelism=KDF_PARALLELISM,
        hash_len=32,
        type=Type.ID,
    )
    return Fernet(base64.urlsafe_b64encode(raw))


class Rotator:
    """Decrypt-with-old / encrypt-with-new, classifying every value."""

    def __init__(self, old_f: Fernet, new_f: Fernet):
        self.old_f = old_f
        self.new_f = new_f
        self.rotated = 0
        self.already = 0
        self.failed = 0

    def rotate(self, token: str) -> str | None:
        """Return new ciphertext, or None to leave the stored value unchanged."""
        raw = token.encode("ascii")
        try:
            plain = self.old_f.decrypt(raw)
        except (InvalidToken, ValueError):
            # Not old-salt ciphertext. Already migrated? Then leave it be.
            try:
                self.new_f.decrypt(raw)
                self.already += 1
            except (InvalidToken, ValueError):
                self.failed += 1
            return None
        self.rotated += 1
        return self.new_f.encrypt(plain).decode("ascii")


def _backup(db_path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    # App is stopped during migration; copy the main DB plus any WAL sidecars.
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if src.exists():
            shutil.copy2(src, str(src) + ".bak")


def _migrate_api_keys(db_path: Path, rot: Rotator, dry_run: bool) -> None:
    if not db_path.exists():
        print(f"  {db_path.name}: not found — skipped")
        return
    _backup(db_path, dry_run)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        rows = conn.execute(
            "SELECT id, enc_secret FROM api_keys WHERE enc_secret IS NOT NULL AND enc_secret != ''"
        ).fetchall()
        for key_id, enc in rows:
            new_token = rot.rotate(enc)
            if new_token is not None and not dry_run:
                conn.execute("UPDATE api_keys SET enc_secret=? WHERE id=?", (new_token, key_id))
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    print(f"  {db_path.name}: {len(rows)} row(s) scanned")


def _migrate_settings(db_path: Path, rot: Rotator, dry_run: bool) -> None:
    if not db_path.exists():
        print(f"  {db_path.name}: not found — skipped")
        return
    _backup(db_path, dry_run)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    scanned = 0
    try:
        for key in SECRET_SETTING_KEYS:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if not row or not row[0]:
                continue
            scanned += 1
            new_token = rot.rotate(row[0])
            if new_token is not None and not dry_run:
                conn.execute("UPDATE settings SET value=? WHERE key=?", (new_token, key))
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    print(f"  {db_path.name}: {scanned} secret setting(s) scanned")


def _migrate_immich_pulls(db_path: Path, rot: Rotator, dry_run: bool) -> None:
    if not db_path.exists():
        print(f"  {db_path.name}: not found — skipped")
        return
    _backup(db_path, dry_run)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    scanned = 0
    try:
        rows = conn.execute("SELECT id, config_json FROM pulls").fetchall()
        for pull_id, cfg_json in rows:
            try:
                cfg = json.loads(cfg_json)
            except (ValueError, TypeError):
                continue
            enc = cfg.get("api_key")
            if not enc:
                continue
            scanned += 1
            new_token = rot.rotate(enc)
            if new_token is not None and not dry_run:
                cfg["api_key"] = new_token
                conn.execute("UPDATE pulls SET config_json=? WHERE id=?", (json.dumps(cfg), pull_id))
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    print(f"  {db_path.name}: {scanned} pull key(s) scanned")


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-encrypt at-rest secrets for the Screensaver→Mosaic rebrand")
    ap.add_argument("--config-dir", default="config", help="Directory holding *.db and secret.key (default: config)")
    ap.add_argument("--dry-run", action="store_true", help="Report what would change without writing or backing up")
    args = ap.parse_args()

    if OLD_SALT == NEW_SALT:
        print("OLD_SALT == NEW_SALT — nothing to migrate.")
        return 0

    config_dir = Path(args.config_dir)

    secret = _resolve_secret(config_dir)
    if secret is None:
        print(
            f"ERROR: no encryption secret available — neither API_KEY_ENCRYPTION_SECRET/"
            f"SESSION_SECRET in the environment nor {config_dir / 'secret.key'} exists.\n"
            f"Run with the same secret the app uses (e.g. 'set -a; source .env; set +a')."
        )
        return 1

    has_env = bool(os.environ.get("API_KEY_ENCRYPTION_SECRET") or os.environ.get("SESSION_SECRET"))
    source = "environment" if has_env else str(config_dir / "secret.key")
    print(f"Encryption secret resolved from: {source}")
    print(f"Salt: {OLD_SALT.decode()} → {NEW_SALT.decode()}")
    if args.dry_run:
        print("DRY RUN — no files will be written or backed up.\n")

    old_f = _derive(secret, OLD_SALT)
    new_f = _derive(secret, NEW_SALT)
    rot = Rotator(old_f, new_f)

    print("Scanning secret stores:")
    _migrate_api_keys(config_dir / "api_keys.db", rot, args.dry_run)
    _migrate_settings(config_dir / "settings.db", rot, args.dry_run)
    _migrate_immich_pulls(config_dir / "immich_pulls.db", rot, args.dry_run)

    print()
    print(f"Re-encrypted : {rot.rotated}")
    print(f"Already new  : {rot.already} (left unchanged)")
    print(f"Undecryptable: {rot.failed} (left unchanged)")
    if rot.failed:
        print(
            "  ⚠ Some values decrypt with neither the old nor new salt — likely encrypted\n"
            "    under a different secret. Those secrets must be re-entered in the admin UI."
        )
    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to apply (backs up each *.db first).")
    else:
        print("\nDone. Backups written alongside each DB as *.db.bak. Start the Mosaic app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
