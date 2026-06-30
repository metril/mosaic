#!/usr/bin/env python3
"""One-shot migration: Immich pull configs from YAML into SQLite.

Run once during the upgrade that moves pulls from
``config/immich_pulls.yaml`` to ``config/immich_pulls.db``. Not part of app
startup — run it deliberately, verify, then deploy.

    python3 scripts/migrate_immich_pulls.py [--config-dir config]

The per-pull ``api_key`` is Fernet-encrypted before storage (same key_crypto as
API keys), so the DB never holds the raw token. On success the YAML is renamed
``immich_pulls.yaml.migrated``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.config_store import ConfigStore  # noqa: E402
from app.auth.key_crypto import encrypt_secret  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate Immich pulls YAML -> SQLite")
    ap.add_argument("--config-dir", default="config")
    args = ap.parse_args()

    config_dir = Path(args.config_dir)
    yaml_path = config_dir / "immich_pulls.yaml"
    db_path = config_dir / "immich_pulls.db"

    if not yaml_path.exists():
        print(f"No {yaml_path} found — nothing to migrate.")
        return 0

    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f) or {}
    pulls = config.get("pulls", {})
    if not isinstance(pulls, dict):
        pulls = {}

    store = ConfigStore(db_path, "pulls")
    existing = set(store.load_all().keys())

    migrated = 0
    skipped = 0
    for name, pull_dict in pulls.items():
        if name in existing:
            skipped += 1
            continue
        pull_dict = dict(pull_dict)
        if pull_dict.get("api_key"):
            pull_dict["api_key"] = encrypt_secret(pull_dict["api_key"])
        store.upsert(name, pull_dict)
        migrated += 1

    backup = yaml_path.with_suffix(yaml_path.suffix + ".migrated")
    yaml_path.rename(backup)

    print(f"Migrated {migrated} pull(s) into {db_path} (skipped {skipped} already present).")
    print(f"Renamed {yaml_path} -> {backup} (api_key encrypted at rest).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
