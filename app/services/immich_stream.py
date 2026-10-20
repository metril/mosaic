"""Immich "stream" pull mode: no disk, in-memory render cache.

A stream pull indexes matching Immich asset ids (SQLite, in the curation DB) and
exposes them as a *virtual folder* of ``<target_folder>/<assetId>.<ext>`` paths.
Nothing is written under IMAGE_DIR; each display image is fetched from Immich on
demand, fit-within resized / re-encoded per the pull's settings and kept in a
bounded in-memory LRU.

  StreamIndex   — persistent folder -> assets map (path lookup, listing)
  StreamFetcher — upstream fetch (+ source fallbacks), render, cache, and
                  single-flight de-duplication of concurrent requests
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import httpx
from PIL import Image, ImageOps

from app.utils.logging import logger
from app.utils.lru_cache import BoundedLRU

# Mimes a browser/Pillow can use as-is; anything else (HEIC, RAW, ...) must come
# from Immich's generated fullsize/preview JPEG instead of /original.
NATIVE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MIME_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
_EXT_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
             "webp": "image/webp", "gif": "image/gif"}
_FORMAT_CT = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

THUMB_CACHE_BYTES = 32 * 1024 * 1024


class StreamError(Exception):
    """Base class for stream fetch failures."""


class StreamNotFound(StreamError):
    """The asset (or its pull) no longer exists -> 404."""


class StreamUpstreamError(StreamError):
    """Immich is unreachable or returned an error -> 502."""


@dataclass
class StreamAsset:
    pull_name: str
    folder: str                      # target_folder, no leading/trailing slash
    asset_id: str
    filename: str = ""               # original file name (display only)
    ext: str = "jpg"                 # virtual path extension
    mime: str = "image/jpeg"         # original asset mime
    width: Optional[int] = None
    height: Optional[int] = None
    is_favorite: bool = False
    taken_at: Optional[str] = None   # ISO timestamp
    city: Optional[str] = None
    country: Optional[str] = None

    @property
    def path(self) -> str:
        return f"{self.folder}/{self.asset_id}.{self.ext}" if self.folder else f"{self.asset_id}.{self.ext}"


def asset_from_search(pull_name: str, folder: str, raw: Dict[str, Any]) -> StreamAsset:
    """Build an index row from an Immich search/metadata asset dict."""
    asset_id = raw["id"]
    filename = raw.get("originalFileName") or ""
    mime = (raw.get("originalMimeType") or "").lower()
    if not mime:
        mime = _EXT_MIME.get(PurePosixPath(filename).suffix.lstrip(".").lower(), "image/jpeg")
    # Only native mimes keep their own extension; others are served as JPEG.
    ext = _MIME_EXT.get(mime, "jpg")
    exif = raw.get("exifInfo") or {}
    return StreamAsset(
        pull_name=pull_name,
        folder=folder.strip("/"),
        asset_id=asset_id,
        filename=filename,
        ext=ext,
        mime=mime,
        width=exif.get("exifImageWidth"),
        height=exif.get("exifImageHeight"),
        is_favorite=bool(raw.get("isFavorite")),
        taken_at=exif.get("dateTimeOriginal") or raw.get("localDateTime") or raw.get("fileCreatedAt"),
        city=exif.get("city"),
        country=exif.get("country"),
    )


class StreamIndex:
    """SQLite-backed index of streamed assets with an in-memory path map.

    Lives in the curation DB file. Reads (lookup / listing) hit memory only;
    writes go through SQLite first, then update memory.
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._by_path: Dict[str, StreamAsset] = {}
        self._folders: Dict[str, Dict[str, StreamAsset]] = {}   # folder -> {path: asset}
        self._init_db()
        self._load()

    # ---- storage ---------------------------------------------------------- #

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS stream_assets (
                    pull_name TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    folder TEXT NOT NULL DEFAULT '',
                    filename TEXT,
                    ext TEXT,
                    mime TEXT,
                    w INTEGER,
                    h INTEGER,
                    is_favorite INTEGER NOT NULL DEFAULT 0,
                    taken_at TEXT,
                    city TEXT,
                    country TEXT,
                    PRIMARY KEY (pull_name, asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_stream_folder ON stream_assets(folder);
                """
            )

    _COLS = "pull_name, asset_id, folder, filename, ext, mime, w, h, is_favorite, taken_at, city, country"

    def _load(self) -> None:
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT {self._COLS} FROM stream_assets").fetchall()
        with self._lock:
            self._by_path.clear()
            self._folders.clear()
            for r in rows:
                self._add_mem(StreamAsset(
                    pull_name=r[0], asset_id=r[1], folder=r[2], filename=r[3] or "",
                    ext=r[4] or "jpg", mime=r[5] or "image/jpeg", width=r[6], height=r[7],
                    is_favorite=bool(r[8]), taken_at=r[9], city=r[10], country=r[11],
                ))
        if rows:
            logger.info(f"Stream index loaded: {len(rows)} assets in {len(self._folders)} folders")

    def _add_mem(self, a: StreamAsset) -> None:
        self._by_path[a.path] = a
        self._folders.setdefault(a.folder, {})[a.path] = a

    def _del_mem(self, a: StreamAsset) -> None:
        self._by_path.pop(a.path, None)
        bucket = self._folders.get(a.folder)
        if bucket is not None:
            bucket.pop(a.path, None)
            if not bucket:
                del self._folders[a.folder]

    @staticmethod
    def _row(a: StreamAsset) -> tuple:
        return (a.pull_name, a.asset_id, a.folder, a.filename, a.ext, a.mime, a.width,
                a.height, int(a.is_favorite), a.taken_at, a.city, a.country)

    # ---- queries ---------------------------------------------------------- #

    @staticmethod
    def _norm(path: str) -> str:
        return (path or "").strip().strip("/")

    def is_stream_folder(self, folder: str) -> bool:
        return self._norm(folder) in self._folders

    def stream_folders(self) -> List[str]:
        return list(self._folders.keys())

    def lookup(self, path: str) -> Optional[StreamAsset]:
        return self._by_path.get(self._norm(path))

    def list_folder(self, folder: str, recursive: bool = False) -> List[str]:
        """Virtual relative paths in ``folder`` (and sub-folders when recursive).
        An empty folder means the library root."""
        folder = self._norm(folder)
        with self._lock:
            out: List[str] = []
            for f, bucket in self._folders.items():
                if f == folder or (recursive and (not folder or f.startswith(folder + "/"))):
                    out.extend(bucket.keys())
            return out

    def assets_for_pull(self, pull_name: str) -> List[StreamAsset]:
        with self._lock:
            return [a for a in self._by_path.values() if a.pull_name == pull_name]

    def count(self, pull_name: str) -> int:
        with self._lock:
            return sum(1 for a in self._by_path.values() if a.pull_name == pull_name)

    # ---- mutations -------------------------------------------------------- #

    def replace(self, pull_name: str, assets: Iterable[StreamAsset],
                keep_paths: Optional[Set[str]] = None) -> int:
        """Replace a pull's rows with ``assets``. Existing rows whose virtual
        path is in ``keep_paths`` (favorites) survive. Returns the new count."""
        assets = list(assets)
        keep_paths = keep_paths or set()
        with self._lock:
            old = [a for a in self._by_path.values() if a.pull_name == pull_name]
            kept = [a for a in old if a.path in keep_paths]
            new_ids = {a.asset_id for a in assets}
            kept = [a for a in kept if a.asset_id not in new_ids]
            final = assets + kept
            with self._connect() as conn:
                conn.execute("DELETE FROM stream_assets WHERE pull_name=?", (pull_name,))
                conn.executemany(
                    f"INSERT OR REPLACE INTO stream_assets ({self._COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [self._row(a) for a in final],
                )
            for a in old:
                self._del_mem(a)
            for a in final:
                self._add_mem(a)
            return len(final)

    def upsert(self, assets: Iterable[StreamAsset]) -> int:
        """Insert/update rows (delta runs) without touching other rows."""
        assets = list(assets)
        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    f"INSERT OR REPLACE INTO stream_assets ({self._COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [self._row(a) for a in assets],
                )
            for a in assets:
                prev = next((p for p in self._folders.get(a.folder, {}).values()
                             if p.pull_name == a.pull_name and p.asset_id == a.asset_id), None)
                if prev is not None:
                    self._del_mem(prev)
                self._add_mem(a)
        return len(assets)

    def drop(self, pull_name: str) -> int:
        """Remove every row for a pull. Returns the number removed."""
        with self._lock:
            old = [a for a in self._by_path.values() if a.pull_name == pull_name]
            with self._connect() as conn:
                conn.execute("DELETE FROM stream_assets WHERE pull_name=?", (pull_name,))
            for a in old:
                self._del_mem(a)
            return len(old)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def fit_within(w: int, h: int, max_w: int, max_h: int) -> Tuple[int, int]:
    """Largest size fitting in max_w x max_h keeping aspect; 0 = unbounded; never upscales."""
    scale = 1.0
    if max_w > 0:
        scale = min(scale, max_w / w)
    if max_h > 0:
        scale = min(scale, max_h / h)
    if scale >= 1.0:
        return w, h
    return max(1, round(w * scale)), max(1, round(h * scale))


def render_image(data: bytes, max_w: int, max_h: int, fmt: str, quality: int) -> Tuple[bytes, str]:
    """Fit-within resize (no upscaling) and encode. ``fmt`` is original|jpeg|png|webp.

    With ``original`` and nothing to change the fetched bytes pass through
    untouched. Runs on the image executor (CPU-bound).
    """
    img = Image.open(BytesIO(data))
    src_format = (img.format or "JPEG").upper()
    if src_format == "MPO":  # multi-picture JPEG (some phones) is plain JPEG to us
        src_format = "JPEG"
    orientation = img.getexif().get(0x0112, 1) if hasattr(img, "getexif") else 1
    target = fit_within(img.width, img.height, max_w, max_h)
    needs_resize = target != (img.width, img.height)
    src_ct = Image.MIME.get(src_format, "image/jpeg")

    if fmt == "original" and not needs_resize and orientation in (None, 1):
        return data, src_ct

    img = ImageOps.exif_transpose(img)
    if needs_resize:
        target = fit_within(img.width, img.height, max_w, max_h)
        img = img.resize(target, Image.Resampling.LANCZOS)

    out_fmt = fmt if fmt in _FORMAT_CT else {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}.get(src_format, "png")
    kwargs: Dict[str, Any] = {}
    icc = img.info.get("icc_profile")
    if icc:
        kwargs["icc_profile"] = icc
    if out_fmt == "jpeg":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        kwargs.update(quality=quality, optimize=True)
    elif out_fmt == "webp":
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.mode else "RGB")
        kwargs.update(quality=quality)
    else:
        if img.mode not in ("RGB", "RGBA", "L", "LA"):
            img = img.convert("RGBA")
        kwargs.update(optimize=True)
    buf = BytesIO()
    img.save(buf, format=out_fmt.upper(), **kwargs)
    return buf.getvalue(), _FORMAT_CT[out_fmt]


@dataclass
class StreamImage:
    data: bytes
    content_type: str
    key: str                         # cache key; also the ETag basis


class StreamFetcher:
    """Fetch, render and memory-cache streamed images.

    ``get_pull(name)`` returns the (decrypted-key) ImmichPullConfig, so a config
    edit takes effect immediately and no extra copy of the API key is kept.
    """

    def __init__(self, get_pull: Callable[[str], Any], executor=None,
                 cache_mb: int = 256, timeout: float = 30.0):
        self._get_pull = get_pull
        self._executor = executor
        self._timeout = timeout
        self.cache = BoundedLRU(max(0, cache_mb) * 1024 * 1024)
        self.thumb_cache = BoundedLRU(THUMB_CACHE_BYTES)
        self._client: Optional[httpx.AsyncClient] = None
        self._inflight: Dict[str, asyncio.Task] = {}
        self.hits = 0
        self.misses = 0

    # ---- lifecycle -------------------------------------------------------- #

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def set_cache_mb(self, mb: int) -> None:
        """Rebuild the render cache with a new budget (drops current entries)."""
        self.cache = BoundedLRU(max(0, mb) * 1024 * 1024)

    def clear_pull(self, pull_name: str) -> None:
        self.cache.clear_prefix(f"{pull_name}|")
        self.thumb_cache.clear_prefix(f"{pull_name}|")

    def stats(self) -> Dict[str, Any]:
        s = self.cache.stats()
        return {"entries": s["entries"], "bytes": s["bytes"], "max_bytes": s["max_bytes"],
                "hits": self.hits, "misses": self.misses}

    # ---- keys / planning --------------------------------------------------- #

    @staticmethod
    def cache_key(cfg, asset: StreamAsset) -> str:
        return (f"{asset.pull_name}|{asset.asset_id}|{cfg.stream_source}|"
                f"{cfg.stream_max_w}x{cfg.stream_max_h}|{cfg.stream_format}|{cfg.stream_quality}")

    def key_for(self, asset: StreamAsset) -> Optional[str]:
        """Cache key (ETag basis) without fetching; None if the pull is gone."""
        cfg = self._get_pull(asset.pull_name)
        return self.cache_key(cfg, asset) if cfg else None

    @staticmethod
    def plan_sources(source: str, mime: str) -> List[str]:
        """Ordered Immich endpoints to try. /original only for browser-native
        mimes; a 4xx on one falls through to the next."""
        if source == "original" and mime in NATIVE_MIMES:
            return ["original", "fullsize", "preview"]
        if source in ("original", "fullsize"):
            return ["fullsize", "preview"]
        return ["preview"]

    # ---- upstream --------------------------------------------------------- #

    @staticmethod
    def _url(cfg, asset_id: str, source: str) -> str:
        base = f"{cfg.server_url.rstrip('/')}/api/assets/{asset_id}"
        if source == "original":
            return f"{base}/original"
        return f"{base}/thumbnail?size={source}"

    async def _get(self, cfg, url: str) -> httpx.Response:
        try:
            return await self._http().get(url, headers={"x-api-key": cfg.api_key})
        except httpx.HTTPError as e:
            raise StreamUpstreamError(f"Immich request failed: {e}") from e

    async def _fetch_upstream(self, cfg, asset: StreamAsset) -> bytes:
        sources = self.plan_sources(cfg.stream_source, asset.mime)
        last_status = 0
        for i, source in enumerate(sources):
            resp = await self._get(cfg, self._url(cfg, asset.asset_id, source))
            if resp.status_code == 200:
                return resp.content
            if 500 <= resp.status_code < 600:
                raise StreamUpstreamError(f"Immich returned {resp.status_code}")
            last_status = resp.status_code
            logger.debug(f"Stream {asset.asset_id}: {source} -> {resp.status_code}, "
                         f"{'trying next source' if i + 1 < len(sources) else 'no more sources'}")
        if last_status == 404:
            raise StreamNotFound(asset.asset_id)
        raise StreamUpstreamError(f"Immich returned {last_status}")

    # ---- public API -------------------------------------------------------- #

    async def get_bytes(self, asset: StreamAsset) -> StreamImage:
        """Rendered bytes for an asset. Concurrent callers for the same key share
        one upstream fetch + render."""
        cfg = self._get_pull(asset.pull_name)
        if cfg is None:
            raise StreamNotFound(asset.pull_name)
        key = self.cache_key(cfg, asset)
        cached = self.cache.get(key)
        if cached is not None:
            self.hits += 1
            return StreamImage(cached[0], cached[1], key)

        task = self._inflight.get(key)
        if task is None:
            self.misses += 1
            task = asyncio.ensure_future(self._produce(cfg, asset, key))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _produce(self, cfg, asset: StreamAsset, key: str) -> StreamImage:
        raw = await self._fetch_upstream(cfg, asset)
        loop = asyncio.get_running_loop()
        try:
            data, ct = await loop.run_in_executor(
                self._executor, render_image, raw, cfg.stream_max_w, cfg.stream_max_h,
                cfg.stream_format, cfg.stream_quality,
            )
        except Exception as e:
            raise StreamUpstreamError(f"Could not decode image from Immich: {e}") from e
        self.cache.put(key, (data, ct), len(data))
        return StreamImage(data, ct, key)

    async def get_thumbnail(self, asset: StreamAsset) -> Tuple[bytes, str]:
        """Immich's own small thumbnail, proxied through a small memory LRU."""
        cfg = self._get_pull(asset.pull_name)
        if cfg is None:
            raise StreamNotFound(asset.pull_name)
        key = f"{asset.pull_name}|{asset.asset_id}|thumb"
        cached = self.thumb_cache.get(key)
        if cached is not None:
            return cached
        resp = await self._get(cfg, self._url(cfg, asset.asset_id, "thumbnail"))
        if resp.status_code == 404:
            raise StreamNotFound(asset.asset_id)
        if resp.status_code != 200:
            raise StreamUpstreamError(f"Immich returned {resp.status_code}")
        ct = resp.headers.get("content-type", "image/webp").split(";")[0]
        self.thumb_cache.put(key, (resp.content, ct), len(resp.content))
        return resp.content, ct
