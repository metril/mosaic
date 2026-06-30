#!/usr/bin/env python3
"""One-shot migration: API keys from the legacy YAML store into SQLite.

Run once during the upgrade that moves keys from ``config/api_keys.yaml`` to
``config/api_keys.db``. This is intentionally NOT part of application startup —
run it deliberately, verify, then deploy the new app.

    python3 scripts/migrate_api_keys.py [--config-dir config]

Migrated keys keep their Argon2 hash (so they continue to authenticate) but have
no recoverable secret (``enc_secret`` is NULL) — the original ``sk_…`` was never
stored. Rotate or recreate such a key to make it recoverable. On success the YAML
is renamed to ``api_keys.yaml.migrated`` so it isn't picked up or migrated again.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml

# Allow running from the repo root: `python3 scripts/migrate_api_keys.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.api_keys import APIKey, APIKeyManager  # noqa: E402


def _parse_dt(value) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    if isinstance(value, datetime):
        return value
    return datetime.now()


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate API keys YAML -> SQLite")
    ap.add_argument("--config-dir", default="config", help="Directory holding api_keys.yaml / api_keys.db")
    args = ap.parse_args()

    config_dir = Path(args.config_dir)
    yaml_path = config_dir / "api_keys.yaml"
    db_path = config_dir / "api_keys.db"

    if not yaml_path.exists():
        print(f"No {yaml_path} found — nothing to migrate.")
        return 0

    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f) or {}
    yaml_keys = config.get("api_keys", [])
    if not isinstance(yaml_keys, list):
        yaml_keys = []

    manager = APIKeyManager(db_path)
    # Load existing rows so the migration is idempotent (skip ids already there).
    import asyncio
    asyncio.run(manager.load_config())
    existing = {k.id for k in manager.list_keys()}

    migrated = 0
    skipped = 0
    for kd in yaml_keys:
        kid = kd.get("id")
        if not kid:
            continue
        if kid in existing:
            skipped += 1
            continue
        api_key = APIKey(
            id=kid,
            name=kd.get("name", "Unnamed Key"),
            key_hash=kd["key_hash"],
            key_prefix=kd.get("key_prefix", "sk_****"),
            created_at=_parse_dt(kd.get("created_at")),
            last_used=_parse_dt(kd["last_used"]) if kd.get("last_used") else None,
            scopes=kd.get("scopes", ["images:read"]),
            enabled=kd.get("enabled", True),
            lookup_prefix=kd.get("lookup_prefix", ""),
            encrypted_key=None,  # legacy secret is unrecoverable
        )
        manager.keys[api_key.id] = api_key
        if api_key.lookup_prefix:
            manager._lookup_index.setdefault(api_key.lookup_prefix, []).append(api_key.id)
        manager._persist(api_key)
        migrated += 1

    backup = yaml_path.with_suffix(yaml_path.suffix + ".migrated")
    yaml_path.rename(backup)

    print(f"Migrated {migrated} key(s) into {db_path} (skipped {skipped} already present).")
    print(f"Renamed {yaml_path} -> {backup}")
    print("Note: migrated keys are non-recoverable (rotate to enable display-URL links).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
