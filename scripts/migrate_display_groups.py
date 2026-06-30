#!/usr/bin/env python3
"""One-shot migration: display groups from YAML into SQLite.

Run once during the upgrade that moves groups from
``config/display_groups.yaml`` to ``config/display_groups.db``. Not part of app
startup — run it deliberately, verify, then deploy.

    python3 scripts/migrate_display_groups.py [--config-dir config]

The stored JSON blob is identical to the old YAML payload per group (settings +
persistent endpoints), so nothing about a group changes except the medium. On
success the YAML is renamed ``display_groups.yaml.migrated``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.config_store import ConfigStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate display groups YAML -> SQLite")
    ap.add_argument("--config-dir", default="config")
    args = ap.parse_args()

    config_dir = Path(args.config_dir)
    yaml_path = config_dir / "display_groups.yaml"
    db_path = config_dir / "display_groups.db"

    if not yaml_path.exists():
        print(f"No {yaml_path} found — nothing to migrate.")
        return 0

    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f) or {}
    groups = config.get("groups", {})
    if not isinstance(groups, dict):
        groups = {}

    store = ConfigStore(db_path, "groups")
    existing = set(store.load_all().keys())

    migrated = 0
    skipped = 0
    for group_id, group_config in groups.items():
        if group_id in existing:
            skipped += 1
            continue
        store.upsert(group_id, group_config)
        migrated += 1

    backup = yaml_path.with_suffix(yaml_path.suffix + ".migrated")
    yaml_path.rename(backup)

    print(f"Migrated {migrated} group(s) into {db_path} (skipped {skipped} already present).")
    print(f"Renamed {yaml_path} -> {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
