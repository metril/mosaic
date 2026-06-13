"""Thread-safe, size-bounded LRU cache.

Used for server-side caches of expensive-to-produce, deterministic artifacts
(processed images, blur data-URLs). Keyed by a string that encodes everything
affecting the output (path + mtime + params), so a changed source file simply
produces a new key; stale entries fall out by LRU eviction. Bounded by an
approximate total byte budget rather than entry count, since item sizes vary
widely (a thumbnail vs. a full-res JPEG).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Optional, Tuple


class BoundedLRU:
    def __init__(self, max_bytes: int):
        # max_bytes <= 0 disables the cache entirely.
        self._max_bytes = max_bytes
        self._store: "OrderedDict[str, Tuple[Any, int]]" = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._max_bytes > 0

    def get(self, key: str) -> Optional[Any]:
        if self._max_bytes <= 0:
            return None
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            self._store.move_to_end(key)
            return item[0]

    def put(self, key: str, value: Any, size: int) -> None:
        if self._max_bytes <= 0:
            return
        # Never cache a single item larger than the whole budget.
        if size > self._max_bytes:
            return
        with self._lock:
            existing = self._store.get(key)
            if existing is not None:
                self._size -= existing[1]
                del self._store[key]
            self._store[key] = (value, size)
            self._size += size
            # Evict least-recently-used until under budget.
            while self._size > self._max_bytes and self._store:
                _, (_, evicted_size) = self._store.popitem(last=False)
                self._size -= evicted_size

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._size = 0

    def clear_prefix(self, prefix: str) -> None:
        """Drop every entry whose key starts with `prefix` (e.g. an image path),
        used to invalidate a folder/file on a filesystem change."""
        if self._max_bytes <= 0:
            return
        with self._lock:
            doomed = [k for k in self._store if k.startswith(prefix)]
            for k in doomed:
                self._size -= self._store[k][1]
                del self._store[k]

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._store), "bytes": self._size, "max_bytes": self._max_bytes}
