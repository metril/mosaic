#!/usr/bin/env python3
"""Re-encrypt stored API key secrets after rotating the encryption secret.

The ``sk_…`` secrets in ``config/api_keys.db`` are Fernet-encrypted with a key
derived from a stable secret (``API_KEY_ENCRYPTION_SECRET`` / ``SESSION_SECRET``
/ ``config/secret.key`` — see app/auth/key_crypto.py). Rotating that derivation
secret makes existing ciphertext undecryptable. Run this BEFORE switching the
app to the new secret: it decrypts every stored secret with the OLD secret and
re-encrypts with the NEW one.

    python3 scripts/reencrypt_api_keys.py \
        --old-secret "$OLD" --new-secret "$NEW" [--config-dir config]

Rows whose ciphertext can't be decrypted with the old secret (already lost, or
migrated/non-recoverable) are left untouched and reported.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.key_crypto import _derive_fernet  # noqa: E402
from cryptography.fernet import InvalidToken  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-encrypt API key secrets with a new encryption secret")
    ap.add_argument("--old-secret", required=True, help="Current encryption secret (decrypts existing rows)")
    ap.add_argument("--new-secret", required=True, help="New encryption secret (re-encrypts rows)")
    ap.add_argument("--config-dir", default="config", help="Directory holding api_keys.db")
    args = ap.parse_args()

    db_path = Path(args.config_dir) / "api_keys.db"
    if not db_path.exists():
        print(f"No {db_path} found — nothing to re-encrypt.")
        return 0

    old_f = _derive_fernet(args.old_secret.encode("utf-8"))
    new_f = _derive_fernet(args.new_secret.encode("utf-8"))

    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        rows = conn.execute(
            "SELECT id, enc_secret FROM api_keys WHERE enc_secret IS NOT NULL"
        ).fetchall()
        reencrypted = 0
        failed = []
        for key_id, enc in rows:
            try:
                plain = old_f.decrypt(enc.encode("ascii"))
            except (InvalidToken, ValueError):
                failed.append(key_id)
                continue
            new_token = new_f.encrypt(plain).decode("ascii")
            conn.execute("UPDATE api_keys SET enc_secret=? WHERE id=?", (new_token, key_id))
            reencrypted += 1
        conn.commit()
    finally:
        conn.close()

    print(f"Re-encrypted {reencrypted} secret(s) in {db_path}.")
    if failed:
        print(f"Could not decrypt {len(failed)} row(s) with the old secret (left unchanged): {failed}")
    print("Now switch the app to the new encryption secret.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
