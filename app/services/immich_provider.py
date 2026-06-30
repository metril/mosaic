"""
Immich Image Provider Service

Pulls images from an Immich server and stores them locally for the screensaver.
"""
import asyncio
import logging
import os
import random
import re
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from app.services.config_store import ConfigStore
from app.auth.key_crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

# Cap stored per-asset error messages so a fully-failed large pull doesn't
# accumulate thousands of strings in memory and in the status response.
MAX_PULL_ERRORS = 50


class PullStatus(str, Enum):
    """Status of a pull operation"""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ImmichPullConfig:
    """Configuration for a single Immich pull operation"""
    name: str                                    # Unique identifier for this pull
    server_url: str                              # Immich server URL
    api_key: str                                 # API key for authentication
    target_folder: str                           # Local folder to store images (relative to image_dir)
    count: int = 50                              # Number of images to pull

    # Search criteria (all optional)
    album_ids: Optional[List[str]] = None        # Pull from specific albums
    album_filter_mode: str = "all"               # "all" (AND) or "any" (OR)
    person_ids: Optional[List[str]] = None       # Filter by people
    person_filter_mode: str = "all"              # "all" (AND) or "any" (OR)
    is_favorite: Optional[bool] = None           # Only favorites
    taken_after: Optional[str] = None            # ISO date string (YYYY-MM-DD)
    taken_before: Optional[str] = None           # ISO date string (YYYY-MM-DD)
    media_type: str = "IMAGE"                    # IMAGE or VIDEO

    # Behavior
    replace_existing: bool = True                # Clear folder before pulling

    # Scheduling (F1) — cron-based auto-pull
    cron_expression: Optional[str] = None        # 5-field cron; None = unscheduled
    cron_timezone: Optional[str] = None          # IANA tz; None = inherit SCHEDULER_TIMEZONE
    last_run: Optional[str] = None               # ISO timestamp of last scheduled run

    # Curation interaction (F6)
    protect_favorites: bool = True               # Keep favorited files across replace_existing pulls
    skip_excluded: bool = True                   # Never re-download excluded assets

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "ImmichPullConfig":
        """Create from dictionary, handling environment variable substitution"""
        # Make a copy to avoid mutating original
        data = data.copy()
        data["name"] = name

        # Substitute environment variables in string values
        for key, value in data.items():
            if isinstance(value, str):
                data[key] = _substitute_env_vars(value)

        # Filter to valid fields
        valid_keys = {"name", "server_url", "api_key", "target_folder", "count",
                      "album_ids", "album_filter_mode", "person_ids", "person_filter_mode",
                      "is_favorite", "taken_after", "taken_before", "media_type",
                      "replace_existing",
                      # F1 scheduling
                      "cron_expression", "cron_timezone", "last_run",
                      # F6 curation interaction
                      "protect_favorites", "skip_excluded"}
        filtered = {k: v for k, v in data.items() if k in valid_keys}

        return cls(**filtered)


@dataclass
class PullResult:
    """Result of a pull operation"""
    pull_name: str
    status: PullStatus = PullStatus.IDLE
    phase: str = "idle"  # "idle", "searching", "downloading", "completed", "cancelled", "failed"
    total: int = 0
    downloaded: int = 0
    search_collected: int = 0  # Assets collected during search phase
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "pull_name": self.pull_name,
            "status": self.status.value,
            "phase": self.phase,
            "total": self.total,
            "downloaded": self.downloaded,
            "search_collected": self.search_collected,
            "skipped": self.skipped,
            "errors": self.errors,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


def _substitute_env_vars(value: str) -> str:
    """Substitute ${VAR} patterns with environment variables"""
    pattern = r'\$\{([^}]+)\}'

    def replace(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return re.sub(pattern, replace, value)


class ImmichProvider:
    """Service for pulling images from Immich"""

    def __init__(
        self,
        image_dir: Path,
        config_path: Optional[Path] = None,
        on_pull_complete: Optional[Callable[[str], Awaitable[None]]] = None,
        curation=None,
        settings=None
    ):
        self.image_dir = image_dir
        self.settings = settings
        # SQLite-backed persistence; the per-pull api_key is encrypted at rest.
        self.config_path = config_path or Path("config/immich_pulls.db")
        self._store = ConfigStore(self.config_path, "pulls")
        # Curation service (F6) — drives skip_excluded / protect_favorites
        self.curation = curation
        self.pulls: Dict[str, ImmichPullConfig] = {}
        self._pull_results: Dict[str, PullResult] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._cancelled_pulls: set = set()
        self._updating_folders: set = set()  # Folders currently being updated
        self._http_client: Optional[httpx.AsyncClient] = None
        self._on_pull_complete = on_pull_complete

    def is_folder_updating(self, folder: str) -> bool:
        """Check if a folder is currently being updated by a pull."""
        return folder in self._updating_folders

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client"""
        if self._http_client is None or self._http_client.is_closed:
            if self.settings is not None:
                timeout = self.settings.get_float("immich_http_timeout")
            else:
                timeout = float(os.environ.get("IMMICH_HTTP_TIMEOUT", "30.0"))
            self._http_client = httpx.AsyncClient(timeout=timeout)
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def load_config(self) -> int:
        """Load pull configurations from the SQLite store.

        The stored ``api_key`` is encrypted at rest; it is decrypted back to the
        plaintext the provider uses in memory. Returns the number of pulls loaded.
        """
        loaded = 0
        for name, pull_dict in self._store.load_all().items():
            try:
                pull_dict = dict(pull_dict)
                enc = pull_dict.get("api_key")
                if enc:
                    pull_dict["api_key"] = decrypt_secret(enc) or ""
                pull_config = ImmichPullConfig.from_dict(name, pull_dict)
                self.pulls[name] = pull_config
                logger.info(f"Loaded Immich pull config: {name}")
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load Immich pull '{name}': {e}")

        return loaded

    def save_config(self) -> int:
        """Persist all pull configurations to the SQLite store (write-through).

        The ``api_key`` is Fernet-encrypted before storage so the DB never holds
        the raw token. Returns the number of pulls saved.
        """
        items: Dict[str, Dict[str, Any]] = {}
        for name, pull in self.pulls.items():
            pull_dict = pull.to_dict()
            del pull_dict["name"]  # name is the row id
            pull_dict = {k: v for k, v in pull_dict.items() if v is not None}
            if pull_dict.get("api_key"):
                pull_dict["api_key"] = encrypt_secret(pull_dict["api_key"])
            items[name] = pull_dict

        self._store.replace_all(items)
        logger.debug(f"Persisted {len(items)} Immich pull configs to {self.config_path}")
        return len(items)

    def add_pull(self, config: ImmichPullConfig) -> None:
        """Add or update a pull configuration"""
        self.pulls[config.name] = config
        logger.info(f"Added Immich pull config: {config.name}")

    def remove_pull(self, name: str) -> bool:
        """Remove a pull configuration"""
        if name in self.pulls:
            del self.pulls[name]
            logger.info(f"Removed Immich pull config: {name}")
            return True
        return False

    def get_pull(self, name: str) -> Optional[ImmichPullConfig]:
        """Get a pull configuration by name"""
        return self.pulls.get(name)

    def list_pulls(self) -> List[Dict[str, Any]]:
        """List all pull configurations"""
        return [pull.to_dict() for pull in self.pulls.values()]

    def get_pull_status(self, name: str) -> Optional[PullResult]:
        """Get the status of a pull operation"""
        return self._pull_results.get(name)

    async def test_connection(self, server_url: str, api_key: str) -> Dict[str, Any]:
        """Test connection to an Immich server.

        Returns server info if successful.
        """
        client = await self._get_client()
        url = f"{server_url.rstrip('/')}/api/server/about"
        headers = {"x-api-key": api_key}

        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            server_info = response.json()

            # Also get album count
            albums_response = await client.get(
                f"{server_url.rstrip('/')}/api/albums",
                headers=headers
            )
            albums_response.raise_for_status()
            albums = albums_response.json()

            return {
                "success": True,
                "server_version": server_info.get("version", "unknown"),
                "album_count": len(albums),
            }
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    async def list_albums(self, server_url: str, api_key: str) -> List[Dict[str, Any]]:
        """List available albums from Immich server"""
        client = await self._get_client()
        url = f"{server_url.rstrip('/')}/api/albums"
        headers = {"x-api-key": api_key}

        response = await client.get(url, headers=headers)
        response.raise_for_status()
        albums = response.json()

        return [
            {
                "id": album["id"],
                "name": album.get("albumName", "Untitled"),
                "asset_count": album.get("assetCount", 0),
            }
            for album in albums
        ]

    async def list_people(self, server_url: str, api_key: str) -> List[Dict[str, Any]]:
        """List recognized people from Immich server"""
        client = await self._get_client()
        url = f"{server_url.rstrip('/')}/api/people"
        headers = {"x-api-key": api_key}

        response = await client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        # API returns {"people": [...], "total": n}
        people = data.get("people", [])

        return [
            {
                "id": person["id"],
                "name": person.get("name", "Unknown"),
                "thumbnail_path": person.get("thumbnailPath"),
            }
            for person in people
        ]

    async def search_assets(
        self,
        config: ImmichPullConfig,
        result: Optional[PullResult] = None
    ) -> List[Dict[str, Any]]:
        """Search Immich for assets with optimal random selection.

        Uses /api/search/statistics to get total count, pre-selects random
        indices, then fetches only the pages containing those indices.

        Supports AND/OR modes for albums and people:
        - "all" mode: assets must match ALL specified albums/people (API default)
        - "any" mode: assets can match ANY specified album/person (union of separate searches)

        Args:
            config: Pull configuration
            result: Optional PullResult to update with search progress
        """
        # Check if we need OR mode for albums or people
        need_album_or = (config.album_filter_mode == "any" and
                         config.album_ids and len(config.album_ids) > 1)
        need_person_or = (config.person_filter_mode == "any" and
                          config.person_ids and len(config.person_ids) > 1)

        if need_album_or or need_person_or:
            return await self._search_assets_or_mode(config, result,
                                                      need_album_or, need_person_or)

        # Standard AND mode - use optimized search
        return await self._search_assets_and_mode(config, result)

    async def _search_assets_and_mode(
        self,
        config: ImmichPullConfig,
        result: Optional[PullResult] = None
    ) -> List[Dict[str, Any]]:
        """Standard AND mode search - assets must match all filters."""
        client = await self._get_client()
        headers = {"x-api-key": config.api_key, "Content-Type": "application/json"}

        base_url = config.server_url.rstrip('/')
        page_size = 1000

        # Build filter body (same for both endpoints)
        filter_body: Dict[str, Any] = {}

        if config.album_ids:
            filter_body["albumIds"] = config.album_ids
        if config.media_type and config.media_type != "ALL":
            filter_body["type"] = config.media_type
        if config.is_favorite is not None:
            filter_body["isFavorite"] = config.is_favorite
        if config.taken_after:
            filter_body["takenAfter"] = f"{config.taken_after}T00:00:00.000Z"
        if config.taken_before:
            filter_body["takenBefore"] = f"{config.taken_before}T23:59:59.999Z"
        if config.person_ids:
            filter_body["personIds"] = config.person_ids

        logger.debug(f"Immich search filters: {filter_body}")

        # Step 1: Get total count from /api/search/statistics
        stats_url = f"{base_url}/api/search/statistics"
        response = await client.post(stats_url, headers=headers, json=filter_body)
        response.raise_for_status()
        stats = response.json()

        total_available = stats.get("total", 0)

        if total_available == 0:
            logger.info("No assets match the search criteria")
            return []

        logger.info(f"Found {total_available} assets matching criteria")

        # Step 2: Pre-select random indices
        count_to_fetch = min(config.count, total_available)
        selected_indices = set(random.sample(range(total_available), count_to_fetch))

        # Group indices by page
        pages_needed: Dict[int, List[int]] = {}
        for idx in selected_indices:
            page = (idx // page_size) + 1
            position = idx % page_size
            if page not in pages_needed:
                pages_needed[page] = []
            pages_needed[page].append(position)

        logger.info(f"Need {len(pages_needed)} pages to get {count_to_fetch} random assets")

        # Step 3: Fetch only required pages
        metadata_url = f"{base_url}/api/search/metadata"
        all_assets: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for page, positions in pages_needed.items():
            body = {**filter_body, "page": page, "size": page_size}

            response = await client.post(metadata_url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()

            assets_data = data.get("assets", {})
            items = assets_data.get("items", [])

            if not items:
                logger.warning(f"Page {page} returned no items (expected {len(positions)} assets)")
                continue

            # Extract only the assets at our selected positions
            for pos in positions:
                if pos < len(items):
                    asset = items[pos]
                    asset_id = asset["id"]
                    if asset_id not in seen_ids:
                        seen_ids.add(asset_id)
                        all_assets.append(asset)

            logger.debug(f"Page {page}: extracted {len(positions)} assets, total {len(all_assets)}/{count_to_fetch}")

            # Update progress
            if result:
                result.search_collected = len(all_assets)
                self._pull_results[result.pull_name] = result

            await asyncio.sleep(0)

        # Shuffle the final order
        random.shuffle(all_assets)

        logger.info(f"Collected {len(all_assets)} random assets from {len(pages_needed)} pages")
        return all_assets

    async def _search_assets_or_mode(
        self,
        config: ImmichPullConfig,
        result: Optional[PullResult],
        need_album_or: bool,
        need_person_or: bool
    ) -> List[Dict[str, Any]]:
        """OR mode search - union results from separate searches.

        For each album/person in OR mode, performs a separate search
        and unions the results (deduplicated by asset ID).
        """
        client = await self._get_client()
        headers = {"x-api-key": config.api_key, "Content-Type": "application/json"}
        base_url = config.server_url.rstrip('/')

        # Build base filter (non-OR filters)
        base_filter: Dict[str, Any] = {}
        if config.media_type and config.media_type != "ALL":
            base_filter["type"] = config.media_type
        if config.is_favorite is not None:
            base_filter["isFavorite"] = config.is_favorite
        if config.taken_after:
            base_filter["takenAfter"] = f"{config.taken_after}T00:00:00.000Z"
        if config.taken_before:
            base_filter["takenBefore"] = f"{config.taken_before}T23:59:59.999Z"

        # Add AND-mode filters (single value or "all" mode with multiple)
        if config.album_ids and not need_album_or:
            base_filter["albumIds"] = config.album_ids
        if config.person_ids and not need_person_or:
            base_filter["personIds"] = config.person_ids

        # Determine what we need to iterate over for OR mode
        album_variants = config.album_ids if need_album_or else [None]
        person_variants = config.person_ids if need_person_or else [None]

        # Collect all assets from OR searches
        all_assets: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # Generate all filter combinations for OR mode
        filter_variants = []
        for album_id in album_variants:
            for person_id in person_variants:
                variant = base_filter.copy()
                if album_id:
                    variant["albumIds"] = [album_id]
                if person_id:
                    variant["personIds"] = [person_id]
                filter_variants.append(variant)

        logger.info(f"OR mode: searching with {len(filter_variants)} filter variants")

        for i, filter_body in enumerate(filter_variants):
            logger.debug(f"OR search variant {i+1}/{len(filter_variants)}: {filter_body}")

            # Get total count for this variant
            stats_url = f"{base_url}/api/search/statistics"
            response = await client.post(stats_url, headers=headers, json=filter_body)
            response.raise_for_status()
            stats = response.json()
            total_for_variant = stats.get("total", 0)

            if total_for_variant == 0:
                continue

            # Fetch assets for this variant (limit to what we need)
            remaining_needed = config.count - len(all_assets)
            if remaining_needed <= 0:
                break

            metadata_url = f"{base_url}/api/search/metadata"
            page_size = 1000

            # Randomly sample indices within this variant instead of always
            # taking the first pages — otherwise large albums only ever
            # contribute their earliest (server-ordered) assets.
            sample_size = min(remaining_needed, total_for_variant)
            selected_indices = random.sample(range(total_for_variant), sample_size)

            pages_needed: Dict[int, List[int]] = {}
            for idx in selected_indices:
                pages_needed.setdefault((idx // page_size) + 1, []).append(idx % page_size)

            for page, positions in pages_needed.items():
                if len(all_assets) >= config.count:
                    break
                body = {**filter_body, "page": page, "size": page_size}
                response = await client.post(metadata_url, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()

                assets_data = data.get("assets", {})
                items = assets_data.get("items", [])

                if not items:
                    continue

                for pos in positions:
                    if pos < len(items):
                        asset = items[pos]
                        asset_id = asset["id"]
                        if asset_id not in seen_ids:
                            seen_ids.add(asset_id)
                            all_assets.append(asset)

                await asyncio.sleep(0)

            # Update progress
            if result:
                result.search_collected = len(all_assets)
                self._pull_results[result.pull_name] = result

        # Shuffle and limit to requested count
        random.shuffle(all_assets)
        all_assets = all_assets[:config.count]

        logger.info(f"OR mode: collected {len(all_assets)} unique assets")
        return all_assets

    async def download_asset(
        self,
        config: ImmichPullConfig,
        asset_id: str,
        original_filename: str,
        target_dir: Optional[Path] = None
    ) -> Optional[Path]:
        """Download a single asset from Immich.

        ``target_dir`` overrides the default destination (used to stage
        downloads in a temp dir for atomic replacement).
        """
        client = await self._get_client()
        url = f"{config.server_url.rstrip('/')}/api/assets/{asset_id}/original"
        headers = {"x-api-key": config.api_key}

        # Strip any directory components from the server-provided filename to
        # prevent path traversal (e.g. "../../etc/cron.d/evil") into a write
        # outside target_dir. Fall back to the asset id if it's empty/dotfile.
        safe_filename = Path(original_filename).name
        if not safe_filename or safe_filename.startswith("."):
            safe_filename = f"{asset_id}.jpg"

        if target_dir is None:
            target_dir = self.image_dir / config.target_folder

        try:
            target_dir.mkdir(parents=True, exist_ok=True)

            # Use original filename, ensuring uniqueness
            target_path = target_dir / safe_filename
            counter = 1
            while target_path.exists():
                stem = Path(safe_filename).stem
                suffix = Path(safe_filename).suffix
                target_path = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            # Stream the response to disk so large images don't buffer fully
            # in memory (a 200-image pull would otherwise spike heap badly).
            with open(target_path, "wb") as f:
                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

            logger.debug(f"Downloaded: {target_path}")
            return target_path

        except Exception as e:
            logger.error(f"Failed to download asset {asset_id}: {e}")
            return None

    async def _search_excluding(self, config, result, excluded_ids: set):
        """Search for assets, dropping excluded ids and backfilling extra
        rounds (up to a cap) to reach the requested count (F6 skip_excluded)."""
        assets = await self.search_assets(config, result)
        if not excluded_ids:
            return assets

        seen = set()
        selected = []
        for a in assets:
            if a["id"] in excluded_ids or a["id"] in seen:
                continue
            seen.add(a["id"])
            selected.append(a)

        # Backfill with additional random search rounds if we came up short
        attempts = 0
        while len(selected) < config.count and attempts < 3:
            attempts += 1
            more = await self.search_assets(config, result)
            added = 0
            for a in more:
                if len(selected) >= config.count:
                    break
                if a["id"] in excluded_ids or a["id"] in seen:
                    continue
                seen.add(a["id"])
                selected.append(a)
                added += 1
            if added == 0:
                break  # nothing new available; stop to avoid spinning
        return selected[:config.count]

    def _carry_over_favorites(self, config, staging_dir: Path) -> int:
        """Copy favorited files from the live target folder into the staging
        dir before an atomic swap, so a replace_existing pull never deletes a
        favorited image (F6 protect_favorites). Returns count preserved."""
        if not config.protect_favorites or self.curation is None:
            return 0
        tf = config.target_folder.strip("/")
        favorites = self.curation.favorite_paths(prefix=tf)
        preserved = 0
        for rel in favorites:
            src = self.image_dir / rel
            if not src.exists() or src.is_symlink():
                continue
            dest = staging_dir / Path(rel).name
            if dest.exists():
                continue  # a freshly-downloaded file already claims this name; keep it
            try:
                staging_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                preserved += 1
            except Exception as e:
                logger.warning(f"Failed to preserve favorite {rel}: {e}")
        return preserved

    async def pull_images(self, pull_name: str) -> PullResult:
        """Execute a pull operation - search, download, store locally"""
        config = self.pulls.get(pull_name)
        if not config:
            result = PullResult(pull_name=pull_name, status=PullStatus.FAILED, phase="failed")
            result.errors.append(f"Pull config not found: {pull_name}")
            return result

        # Initialize result
        result = PullResult(
            pull_name=pull_name,
            status=PullStatus.RUNNING,
            phase="searching",
            started_at=datetime.now(timezone.utc),
        )
        self._pull_results[pull_name] = result

        # Lock the folder to prevent display groups from accessing it during update
        self._updating_folders.add(config.target_folder)
        logger.debug(f"Locked folder for pull: {config.target_folder}")

        final_dir = self.image_dir / config.target_folder
        # Staging dir is hidden ("." prefix) so it is ignored by image scans
        # until the atomic swap below. Sibling of final_dir => same filesystem.
        staging_dir = final_dir.with_name(f".{final_dir.name}.pulltmp")

        try:
            # When replacing, download into a staging dir and swap atomically so
            # a mid-pull failure never leaves the user with an emptied folder.
            # When appending, write straight into the final dir.
            download_dir = staging_dir if config.replace_existing else final_dir

            if config.replace_existing and staging_dir.exists():
                # Clean up a staging dir left over from a previously crashed pull
                shutil.rmtree(staging_dir, ignore_errors=True)

            # Excluded asset ids to skip (F6) — never re-download hidden images
            excluded_ids = set()
            if config.skip_excluded and self.curation is not None:
                excluded_ids = self.curation.excluded_asset_ids()

            # Search for assets (pass result for progress tracking), filtering
            # out excluded ids and backfilling extra rounds to reach the count.
            logger.info(f"Searching Immich for pull: {pull_name}")
            assets = await self._search_excluding(config, result, excluded_ids)
            result.total = len(assets)
            result.phase = "downloading"
            self._pull_results[pull_name] = result
            logger.info(f"Found {len(assets)} assets for pull: {pull_name}")

            cancelled = False
            downloaded_items = []  # (asset_id, basename) for curation asset-map

            # Download each asset
            for asset in assets:
                # Check for cancellation before each download
                if pull_name in self._cancelled_pulls:
                    result.status = PullStatus.CANCELLED
                    result.phase = "cancelled"
                    result.completed_at = datetime.now(timezone.utc)
                    logger.info(f"Pull cancelled: {pull_name} - {result.downloaded}/{result.total} images downloaded before cancellation")
                    self._cancelled_pulls.discard(pull_name)
                    self._pull_results[pull_name] = result
                    cancelled = True
                    break

                asset_id = asset["id"]
                original_filename = asset.get("originalFileName", f"{asset_id}.jpg")

                path = await self.download_asset(config, asset_id, original_filename, target_dir=download_dir)
                if path:
                    result.downloaded += 1
                    downloaded_items.append((asset_id, path.name))
                else:
                    if len(result.errors) < MAX_PULL_ERRORS:
                        result.errors.append(f"Failed to download: {asset_id}")

                # Update status for progress tracking
                self._pull_results[pull_name] = result

                # Yield to event loop to allow status polling and cancellation requests
                await asyncio.sleep(0)

            if cancelled:
                # Discard the half-built staging dir; leave the live folder intact
                if config.replace_existing:
                    shutil.rmtree(staging_dir, ignore_errors=True)
                return result

            # Atomically swap staging dir into place for replace_existing pulls
            if config.replace_existing:
                # Preserve favorited files so a re-pull never deletes them (F6)
                preserved = self._carry_over_favorites(config, staging_dir)
                if final_dir.exists():
                    shutil.rmtree(final_dir)
                staging_dir.replace(final_dir)
                logger.info(f"Swapped staged downloads into: {final_dir} (preserved {preserved} favorites)")

            # Record path -> asset_id map for curation (F6), using post-swap paths
            if self.curation is not None and downloaded_items:
                tf = config.target_folder.strip("/")
                self.curation.record_pull_assets(
                    pull_name,
                    [(f"{tf}/{name}" if tf else name, aid) for (aid, name) in downloaded_items],
                )

            result.status = PullStatus.COMPLETED
            result.phase = "completed"
            result.completed_at = datetime.now(timezone.utc)
            logger.info(f"Pull completed: {pull_name} - {result.downloaded}/{result.total} images")

        except asyncio.CancelledError:
            result.status = PullStatus.CANCELLED
            result.phase = "cancelled"
            result.completed_at = datetime.now(timezone.utc)
            shutil.rmtree(staging_dir, ignore_errors=True)
            logger.info(f"Pull cancelled (task): {pull_name} - {result.downloaded}/{result.total} images downloaded before cancellation")

        except Exception as e:
            result.status = PullStatus.FAILED
            result.phase = "failed"
            if len(result.errors) < MAX_PULL_ERRORS:
                result.errors.append(str(e))
            result.completed_at = datetime.now(timezone.utc)
            # Leave the existing folder untouched; only drop the staging dir
            shutil.rmtree(staging_dir, ignore_errors=True)
            logger.error(f"Pull failed: {pull_name} - {e}")

        finally:
            # Always unlock the folder when done
            self._updating_folders.discard(config.target_folder)
            logger.debug(f"Unlocked folder after pull: {config.target_folder}")

            # Notify display groups to refresh their image pools
            if self._on_pull_complete:
                try:
                    await self._on_pull_complete(config.target_folder)
                except Exception as e:
                    logger.error(f"Error in post-pull refresh for {config.target_folder}: {e}")

        self._pull_results[pull_name] = result
        return result

    async def start_pull(self, pull_name: str) -> PullResult:
        """Start a pull operation in the background.

        Returns immediately with initial status.
        """
        # Check if already running
        if pull_name in self._running_tasks:
            task = self._running_tasks[pull_name]
            if not task.done():
                result = self._pull_results.get(pull_name)
                if result:
                    return result
                return PullResult(pull_name=pull_name, status=PullStatus.RUNNING)

        # Start new task
        task = asyncio.create_task(self.pull_images(pull_name))
        self._running_tasks[pull_name] = task

        # Return initial status
        return PullResult(
            pull_name=pull_name,
            status=PullStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

    async def cancel_pull(self, pull_name: str) -> PullResult:
        """Cancel a running pull operation.

        Sets the cancelled flag so the download loop stops gracefully.
        Returns the updated result.
        """
        if pull_name not in self._running_tasks:
            result = self._pull_results.get(pull_name)
            if result:
                return result
            return PullResult(pull_name=pull_name, status=PullStatus.IDLE)

        task = self._running_tasks[pull_name]
        if task.done():
            result = self._pull_results.get(pull_name)
            if result:
                return result
            return PullResult(pull_name=pull_name, status=PullStatus.IDLE)

        # Signal cancellation via the flag (checked in pull_images loop)
        self._cancelled_pulls.add(pull_name)
        logger.info(f"Cancellation requested for pull: {pull_name}")

        # Wait briefly for the loop to pick up the cancellation
        # If it doesn't stop on its own, force-cancel the task
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        result = self._pull_results.get(pull_name)
        if result and result.status == PullStatus.RUNNING:
            result.status = PullStatus.CANCELLED
            result.completed_at = datetime.now(timezone.utc)
            self._pull_results[pull_name] = result

        self._cancelled_pulls.discard(pull_name)
        self._running_tasks.pop(pull_name, None)

        return result or PullResult(pull_name=pull_name, status=PullStatus.CANCELLED)
