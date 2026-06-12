"""Image curation (favorite / hide-from-slideshow) backed by SQLite.

Stores curation keyed by image path relative to IMAGE_DIR AND, when known, the
Immich asset id that produced the file. Designed to scale: per-toggle writes are
O(1) and lookups are indexed, unlike a rewrite-the-whole-file YAML.

Two tables:
  curation         — sparse, only curated images
  immich_asset_map — path -> asset_id inventory, refreshed at pull time, used to
                     resolve a path to its Immich asset id when the user curates
                     a pulled image from the browse UI (which only knows the path)

Hot-path reads (display filtering) use in-memory cached sets refreshed on write.
sqlite calls are cheap; callers on the event loop should still invoke the
write/scan methods via an executor (see app.api.routes usage).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Optional

from app.utils.logging import logger


class CurationService:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        # In-memory caches for O(1) hot-path lookups (display filter)
        self._excluded_paths: set[str] = set()
        self._excluded_asset_ids: set[str] = set()
        self._favorite_paths: set[str] = set()
        self._init_db()
        self._refresh_caches()

    # ------------------------------------------------------------------ #
    # setup
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
                CREATE TABLE IF NOT EXISTS curation (
                    path TEXT PRIMARY KEY,
                    immich_asset_id TEXT,
                    immich_pull TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    excluded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_curation_asset ON curation(immich_asset_id);
                CREATE INDEX IF NOT EXISTS idx_curation_excluded ON curation(excluded);
                CREATE INDEX IF NOT EXISTS idx_curation_favorite ON curation(favorite);

                CREATE TABLE IF NOT EXISTS immich_asset_map (
                    path TEXT PRIMARY KEY,
                    asset_id TEXT,
                    pull_name TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_assetmap_pull ON immich_asset_map(pull_name);
                """
            )
        logger.info(f"Curation DB ready at {self.db_path}")

    def _refresh_caches(self) -> None:
        with self._lock, self._connect() as conn:
            self._excluded_paths = {
                r[0] for r in conn.execute("SELECT path FROM curation WHERE excluded=1")
            }
            self._favorite_paths = {
                r[0] for r in conn.execute("SELECT path FROM curation WHERE favorite=1")
            }
            self._excluded_asset_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT immich_asset_id FROM curation WHERE excluded=1 AND immich_asset_id IS NOT NULL"
                )
            }

    # ------------------------------------------------------------------ #
    # hot-path lookups (in-memory)
    # ------------------------------------------------------------------ #

    def is_excluded(self, path: str) -> bool:
        return path in self._excluded_paths

    def is_favorite(self, path: str) -> bool:
        return path in self._favorite_paths

    def excluded_paths(self) -> set[str]:
        return set(self._excluded_paths)

    def excluded_asset_ids(self) -> set[str]:
        return set(self._excluded_asset_ids)

    def favorite_paths(self, prefix: Optional[str] = None) -> set[str]:
        if prefix is None:
            return set(self._favorite_paths)
        pfx = prefix.strip("/")
        return {p for p in self._favorite_paths if p == pfx or p.startswith(pfx + "/")}

    def get_state(self, path: str) -> dict:
        return {"favorite": self.is_favorite(path), "excluded": self.is_excluded(path)}

    # ------------------------------------------------------------------ #
    # mutations
    # ------------------------------------------------------------------ #

    def _lookup_asset_id(self, conn: sqlite3.Connection, path: str) -> tuple[Optional[str], Optional[str]]:
        row = conn.execute(
            "SELECT asset_id, pull_name FROM immich_asset_map WHERE path=?", (path,)
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def _set_flag(self, path: str, column: str, value: bool) -> bool:
        """Set favorite/excluded for a path; returns the new value."""
        assert column in ("favorite", "excluded")
        with self._lock, self._connect() as conn:
            asset_id, pull_name = self._lookup_asset_id(conn, path)
            existing = conn.execute(
                "SELECT favorite, excluded FROM curation WHERE path=?", (path,)
            ).fetchone()
            fav = bool(existing[0]) if existing else False
            exc = bool(existing[1]) if existing else False
            if column == "favorite":
                fav = value
            else:
                exc = value
            if not fav and not exc:
                # No flags left — drop the sparse row entirely
                conn.execute("DELETE FROM curation WHERE path=?", (path,))
            else:
                conn.execute(
                    """
                    INSERT INTO curation (path, immich_asset_id, immich_pull, favorite, excluded, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(path) DO UPDATE SET
                        favorite=excluded.favorite,
                        excluded=excluded.excluded,
                        immich_asset_id=COALESCE(curation.immich_asset_id, excluded.immich_asset_id),
                        immich_pull=COALESCE(curation.immich_pull, excluded.immich_pull),
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (path, asset_id, pull_name, int(fav), int(exc)),
                )
        self._refresh_caches()
        return value

    def set_favorite(self, path: str, value: bool) -> bool:
        return self._set_flag(path, "favorite", value)

    def set_excluded(self, path: str, value: bool) -> bool:
        return self._set_flag(path, "excluded", value)

    def toggle_favorite(self, path: str) -> bool:
        return self.set_favorite(path, not self.is_favorite(path))

    def toggle_exclude(self, path: str) -> bool:
        return self.set_excluded(path, not self.is_excluded(path))

    def record_pull_assets(self, pull_name: str, items: Iterable[tuple[str, str]]) -> None:
        """Replace the path->asset_id inventory for a pull (called at pull time)."""
        items = list(items)
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM immich_asset_map WHERE pull_name=?", (pull_name,))
            conn.executemany(
                "INSERT OR REPLACE INTO immich_asset_map (path, asset_id, pull_name) VALUES (?, ?, ?)",
                [(path, asset_id, pull_name) for (path, asset_id) in items],
            )
            # Backfill asset ids onto any existing curation rows for these paths
            for path, asset_id in items:
                conn.execute(
                    "UPDATE curation SET immich_asset_id=?, immich_pull=? WHERE path=? AND immich_asset_id IS NULL",
                    (asset_id, pull_name, path),
                )
        self._refresh_caches()

    def list_all(self) -> dict:
        with self._lock, self._connect() as conn:
            fav = [r[0] for r in conn.execute("SELECT path FROM curation WHERE favorite=1 ORDER BY path")]
            exc = [r[0] for r in conn.execute("SELECT path FROM curation WHERE excluded=1 ORDER BY path")]
        return {"favorites": fav, "excluded": exc}
