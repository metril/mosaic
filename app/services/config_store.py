"""Tiny SQLite-backed store for per-entity JSON config blobs.

Used to persist display groups and Immich pulls (previously YAML) with
write-through durability, mirroring the SQLite pattern of
``CurationService`` / ``APIKeyManager`` (WAL, a process lock, ``_connect`` /
``_init_db``). Each store owns one table of ``(id, config_json)`` rows; the
caller decides the JSON shape (it stays identical to the old YAML payload).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict

from app.utils.logging import logger


class ConfigStore:
    def __init__(self, db_path: Path, table: str):
        self.db_path = Path(db_path)
        # Table name is a fixed internal constant per store, never user input.
        self.table = table
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        logger.info(f"Config store ready: {self.db_path} ({self.table})")

    def load_all(self) -> Dict[str, dict]:
        """Return ``{id: config_dict}`` for every row, preserving insert order."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, config_json FROM {self.table} ORDER BY rowid"
            ).fetchall()
        out: Dict[str, dict] = {}
        for id_, cfg_json in rows:
            try:
                out[id_] = json.loads(cfg_json)
            except (ValueError, TypeError) as e:
                logger.error(f"Bad config_json for {self.table}.{id_}: {e}")
        return out

    def upsert(self, id_: str, cfg: Dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table} (id, config_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    config_json=excluded.config_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (id_, json.dumps(cfg)),
            )

    def replace_all(self, items: Dict[str, Dict[str, Any]]) -> None:
        """Atomically make the table exactly ``items`` (used by bulk save)."""
        with self._lock, self._connect() as conn:
            conn.execute(f"DELETE FROM {self.table}")
            conn.executemany(
                f"INSERT INTO {self.table} (id, config_json) VALUES (?, ?)",
                [(id_, json.dumps(cfg)) for id_, cfg in items.items()],
            )

    def delete(self, id_: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(f"DELETE FROM {self.table} WHERE id=?", (id_,))

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]
