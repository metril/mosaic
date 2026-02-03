"""
Immich Image Provider Service

Pulls images from an Immich server and stores them locally for the screensaver.
"""
import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


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
    album_id: Optional[str] = None               # Pull from specific album
    person_ids: Optional[List[str]] = None       # Filter by people
    is_favorite: Optional[bool] = None           # Only favorites
    taken_after: Optional[str] = None            # ISO date string (YYYY-MM-DD)
    taken_before: Optional[str] = None           # ISO date string (YYYY-MM-DD)
    media_type: str = "IMAGE"                    # IMAGE or VIDEO

    # Behavior
    replace_existing: bool = True                # Clear folder before pulling

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
                      "album_id", "person_ids", "is_favorite", "taken_after",
                      "taken_before", "media_type", "replace_existing"}
        filtered = {k: v for k, v in data.items() if k in valid_keys}

        return cls(**filtered)


@dataclass
class PullResult:
    """Result of a pull operation"""
    pull_name: str
    status: PullStatus = PullStatus.IDLE
    total: int = 0
    downloaded: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "pull_name": self.pull_name,
            "status": self.status.value,
            "total": self.total,
            "downloaded": self.downloaded,
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

    def __init__(self, image_dir: Path, config_path: Optional[Path] = None):
        self.image_dir = image_dir
        self.config_path = config_path or Path("config/immich_pulls.yaml")
        self.pulls: Dict[str, ImmichPullConfig] = {}
        self._pull_results: Dict[str, PullResult] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._cancelled_pulls: set = set()
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client"""
        if self._http_client is None or self._http_client.is_closed:
            timeout = float(os.environ.get("IMMICH_HTTP_TIMEOUT", "30.0"))
            self._http_client = httpx.AsyncClient(timeout=timeout)
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def load_config(self) -> int:
        """Load pull configurations from YAML file.

        Returns the number of pulls loaded.
        """
        if not self.config_path.exists():
            logger.debug(f"Immich config file not found: {self.config_path}")
            return 0

        import yaml
        with open(self.config_path, "r") as f:
            config = yaml.safe_load(f) or {}

        loaded = 0
        for name, pull_dict in config.get("pulls", {}).items():
            try:
                pull_config = ImmichPullConfig.from_dict(name, pull_dict)
                self.pulls[name] = pull_config
                logger.info(f"Loaded Immich pull config: {name}")
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load Immich pull '{name}': {e}")

        return loaded

    def save_config(self) -> int:
        """Save current pull configurations to YAML file.

        Returns the number of pulls saved.
        """
        import yaml

        config: Dict[str, Any] = {"pulls": {}}
        for name, pull in self.pulls.items():
            pull_dict = pull.to_dict()
            # Remove 'name' as it's the key
            del pull_dict["name"]
            # Filter out None values and defaults for cleaner output
            config["pulls"][name] = {k: v for k, v in pull_dict.items() if v is not None}

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Saved {len(self.pulls)} Immich pull configs to: {self.config_path}")
        return len(self.pulls)

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

    async def search_assets(self, config: ImmichPullConfig) -> List[Dict[str, Any]]:
        """Search Immich for random assets matching criteria.

        Uses /api/search/random endpoint which returns random assets each call.
        Makes multiple requests with deduplication to collect the desired count.
        """
        client = await self._get_client()
        headers = {"x-api-key": config.api_key, "Content-Type": "application/json"}

        url = f"{config.server_url.rstrip('/')}/api/search/random"

        body: Dict[str, Any] = {
            "size": min(1000, config.count),  # Max 1000 per request
        }

        # Album filter
        if config.album_id:
            body["albumIds"] = [config.album_id]

        # Media type filter
        if config.media_type and config.media_type != "ALL":
            body["type"] = config.media_type

        # Favorites filter
        if config.is_favorite is not None:
            body["isFavorite"] = config.is_favorite

        # Date filters
        if config.taken_after:
            body["takenAfter"] = f"{config.taken_after}T00:00:00.000Z"
        if config.taken_before:
            body["takenBefore"] = f"{config.taken_before}T23:59:59.999Z"

        # Person filter
        if config.person_ids:
            body["personIds"] = config.person_ids

        logger.debug(f"Immich search body: {body}")

        # Collect assets with deduplication
        seen_ids: set = set()
        all_assets: List[Dict[str, Any]] = []
        max_attempts = (config.count // 500) + 10  # Prevent infinite loop

        for attempt in range(max_attempts):
            if len(all_assets) >= config.count:
                break

            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            assets = response.json()

            if not assets:
                logger.warning(
                    f"No more assets available from Immich "
                    f"(got {len(all_assets)}/{config.count})"
                )
                break

            # Count new assets before adding
            new_count = 0
            for asset in assets:
                if asset["id"] not in seen_ids:
                    seen_ids.add(asset["id"])
                    all_assets.append(asset)
                    new_count += 1
                    if len(all_assets) >= config.count:
                        break

            logger.debug(
                f"Immich search attempt {attempt + 1}: "
                f"got {len(assets)} assets, {new_count} new, "
                f"total {len(all_assets)}/{config.count}"
            )

            # If we got very few new assets, we're likely exhausting the pool
            if new_count < 50 and len(assets) >= body["size"]:
                logger.info(
                    f"Asset pool nearly exhausted (only {new_count} new assets), "
                    f"collected {len(all_assets)}"
                )
                break

        return all_assets[:config.count]

    async def download_asset(
        self,
        config: ImmichPullConfig,
        asset_id: str,
        original_filename: str
    ) -> Optional[Path]:
        """Download a single asset from Immich"""
        client = await self._get_client()
        url = f"{config.server_url.rstrip('/')}/api/assets/{asset_id}/original"
        headers = {"x-api-key": config.api_key}

        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            # Determine target path
            target_dir = self.image_dir / config.target_folder
            target_dir.mkdir(parents=True, exist_ok=True)

            # Use original filename, ensuring uniqueness
            target_path = target_dir / original_filename
            counter = 1
            while target_path.exists():
                stem = Path(original_filename).stem
                suffix = Path(original_filename).suffix
                target_path = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            # Write file
            with open(target_path, "wb") as f:
                f.write(response.content)

            logger.debug(f"Downloaded: {target_path}")
            return target_path

        except Exception as e:
            logger.error(f"Failed to download asset {asset_id}: {e}")
            return None

    async def pull_images(self, pull_name: str) -> PullResult:
        """Execute a pull operation - search, download, store locally"""
        config = self.pulls.get(pull_name)
        if not config:
            result = PullResult(pull_name=pull_name, status=PullStatus.FAILED)
            result.errors.append(f"Pull config not found: {pull_name}")
            return result

        # Initialize result
        result = PullResult(
            pull_name=pull_name,
            status=PullStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self._pull_results[pull_name] = result

        try:
            # Clear target folder if requested
            if config.replace_existing:
                target_dir = self.image_dir / config.target_folder
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                    logger.info(f"Cleared target folder: {target_dir}")

            # Search for assets
            logger.info(f"Searching Immich for pull: {pull_name}")
            assets = await self.search_assets(config)
            result.total = len(assets)
            logger.info(f"Found {len(assets)} assets for pull: {pull_name}")

            # Download each asset
            for asset in assets:
                # Check for cancellation before each download
                if pull_name in self._cancelled_pulls:
                    result.status = PullStatus.CANCELLED
                    result.completed_at = datetime.now(timezone.utc)
                    logger.info(f"Pull cancelled: {pull_name} - {result.downloaded}/{result.total} images downloaded before cancellation")
                    self._cancelled_pulls.discard(pull_name)
                    self._pull_results[pull_name] = result
                    return result

                asset_id = asset["id"]
                original_filename = asset.get("originalFileName", f"{asset_id}.jpg")

                path = await self.download_asset(config, asset_id, original_filename)
                if path:
                    result.downloaded += 1
                else:
                    result.errors.append(f"Failed to download: {asset_id}")

                # Update status for progress tracking
                self._pull_results[pull_name] = result

                # Yield to event loop to allow status polling and cancellation requests
                await asyncio.sleep(0)

            result.status = PullStatus.COMPLETED
            result.completed_at = datetime.now(timezone.utc)
            logger.info(f"Pull completed: {pull_name} - {result.downloaded}/{result.total} images")

        except asyncio.CancelledError:
            result.status = PullStatus.CANCELLED
            result.completed_at = datetime.now(timezone.utc)
            logger.info(f"Pull cancelled (task): {pull_name} - {result.downloaded}/{result.total} images downloaded before cancellation")

        except Exception as e:
            result.status = PullStatus.FAILED
            result.errors.append(str(e))
            result.completed_at = datetime.now(timezone.utc)
            logger.error(f"Pull failed: {pull_name} - {e}")

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
