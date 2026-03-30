"""
Display Manager for Multi-Browser Image Display

Manages image display across multiple browsers using WebSocket connections.
Each browser in a display group displays a unique image, with optional synchronized
switching or on-demand image cycling.
"""

import asyncio
import math
import os
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Any, Literal
from fastapi import WebSocket
from pydantic import BaseModel, ConfigDict, Field

from app.services.image_service import ImageService
from app.utils.blur import create_blur_background
from app.utils.logging import logger
from app.utils.metrics import metrics
from app.utils.path_security import resolve_image_path

# Constants
PRELOAD_LEAD_TIME_SECONDS = int(os.environ.get("PRELOAD_LEAD_TIME", "5"))
MAX_CYCLE_COUNT = 2**31  # Wrap cycle counter to prevent overflow (S14)
MAX_IMAGE_HISTORY = int(os.environ.get("MAX_IMAGE_HISTORY", "100"))
WEBSOCKET_SEND_TIMEOUT = float(os.environ.get("WEBSOCKET_SEND_TIMEOUT", "5.0"))

# Connection limits (A10) - configurable via environment variables
MAX_BROWSERS_PER_GROUP = int(os.environ.get("MAX_BROWSERS_PER_GROUP", "100"))
MAX_GROUPS = int(os.environ.get("MAX_GROUPS", "50"))


class BrowserStatus(str, Enum):
    """Status of a connected browser"""
    CONNECTING = "connecting"
    READY = "ready"
    PRELOADING = "preloading"
    PRELOAD_READY = "preload_ready"
    SWITCHING = "switching"
    DISCONNECTED = "disconnected"


class WebhookTrigger(str, Enum):
    """Events that can trigger a webhook"""
    SETTINGS_CHANGE = "settings_change"
    IMAGE_CYCLE = "image_cycle"
    INTERVAL = "interval"


class WebhookPayloadOption(str, Enum):
    """Data fields that can be included in webhook payload"""
    GROUP_ID = "group_id"
    EVENT_TYPE = "event_type"
    TIMESTAMP = "timestamp"
    SETTINGS = "settings"
    CURRENT_IMAGE = "current_image"
    BROWSER_COUNT = "browser_count"
    CYCLE_NUMBER = "cycle_number"


class WebhookConfig(BaseModel):
    """Configuration for a single webhook"""
    model_config = ConfigDict(extra='ignore', use_enum_values=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    url: str
    enabled: bool = True
    triggers: List[WebhookTrigger] = Field(default_factory=list)
    payload_fields: List[WebhookPayloadOption] = Field(
        default_factory=lambda: [
            WebhookPayloadOption.GROUP_ID,
            WebhookPayloadOption.EVENT_TYPE,
            WebhookPayloadOption.TIMESTAMP
        ]
    )
    interval_minutes: Optional[int] = Field(None, ge=1, le=1440)
    secret: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)


class DisplayGroupSettings(BaseModel):
    """All configurable parameters for a display group"""
    model_config = ConfigDict(extra='ignore')  # Ignore unknown keys from old configs

    # Basic settings
    duration: int = Field(default=30, ge=5, le=3600)
    auto_cycle: bool = True  # When False, images only change on manual request
    folders: Optional[str] = None
    recursive: bool = False
    width: Optional[int] = Field(default=None, ge=1)
    height: Optional[int] = Field(default=None, ge=1)
    preserve_aspect: bool = True
    mode: Literal['fit', 'fill', 'stretch'] = 'fit'
    pad_color: str = 'black'
    format: Optional[str] = None

    # E-Ink settings
    eink: bool = False
    saturation: float = Field(default=2.5, ge=0.0, le=10.0)
    contrast: float = Field(default=1.2, ge=0.0, le=5.0)
    brightness: float = Field(default=1.1, ge=0.0, le=5.0)
    sharpen: bool = True

    # Clock settings
    show_time: bool = False
    show_date: bool = False
    time_format: Literal['12', '24'] = '24'
    date_format: str = 'DDDD, MMMM DD'
    clock_source: Literal['client', 'server', 'homeassistant'] = 'client'
    clock_position: Literal['bottom-left', 'bottom-right', 'top-left', 'top-right'] = 'bottom-left'
    overlay_theme: Literal['fade', 'solid', 'bubble', 'blur'] = 'fade'

    # Weather settings
    show_weather: bool = False
    weather_position: Literal['bottom-left', 'bottom-right', 'top-left', 'top-right'] = 'bottom-right'
    weather_ha_entity: Optional[str] = None
    weather_show_forecast: bool = False
    weather_forecast_type: Literal['daily', 'hourly', 'twice_daily'] = 'daily'
    weather_forecast_days: int = Field(default=3, ge=1, le=24)
    weather_show_high_low: bool = True
    weather_show_precipitation: bool = False

    # Layout settings
    layout: Literal['single', 'splitview', 'splitview-landscape'] = 'single'

    # Background blur settings
    background_blur: bool = False
    background_blur_amount: int = Field(default=50, ge=0, le=100)
    image_fit: Literal['contain', 'cover'] = 'contain'

    # Burn-in prevention settings
    burn_in_protection: bool = False
    burn_in_interval: int = Field(default=5, ge=1, le=60)  # minutes between dimming cycles
    burn_in_duration: int = Field(default=30, ge=5, le=120)  # seconds dimmed
    burn_in_opacity: int = Field(default=70, ge=10, le=90)  # opacity percentage when dimmed
    burn_in_shift_enabled: bool = True  # enable pixel shifting (when burn_in_protection is on)
    burn_in_shift_amount: int = Field(default=5, ge=1, le=20)  # pixels to shift
    burn_in_shift_interval: int = Field(default=60, ge=10, le=300)  # seconds between shifts

    # Webhook settings
    webhooks: List[WebhookConfig] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization (API responses)"""
        return self.model_dump()

    def to_config_dict(self) -> Dict[str, Any]:
        """Convert to dict for YAML config, with nested eink structure."""
        data = self.model_dump(exclude_defaults=True)

        # Nest eink params for cleaner YAML
        eink_keys = ['saturation', 'contrast', 'brightness', 'sharpen']
        eink_vals = {k: data.pop(k) for k in eink_keys if k in data}
        if 'eink' in data or eink_vals:
            eink_dict = {'enabled': data.pop('eink', False)}
            eink_dict.update(eink_vals)
            # Only include eink section if it has non-default values
            if eink_dict != {'enabled': False}:
                data['eink'] = eink_dict

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DisplayGroupSettings":
        """Create from dictionary, handling nested eink section (alias for from_config_dict)"""
        return cls.from_config_dict(data)

    @classmethod
    def from_config_dict(cls, data: Dict[str, Any]) -> "DisplayGroupSettings":
        """Create from YAML config dict, handling nested eink section."""
        data = data.copy()
        if 'eink' in data and isinstance(data['eink'], dict):
            eink = data.pop('eink')
            data['eink'] = eink.get('enabled', True)
            data['saturation'] = eink.get('saturation', 2.5)
            data['contrast'] = eink.get('contrast', 1.2)
            data['brightness'] = eink.get('brightness', 1.1)
            data['sharpen'] = eink.get('sharpen', True)
        return cls.model_validate(data)


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime (P3)."""
    return datetime.now(timezone.utc)


def _next_cron_time(duration: int) -> datetime:
    """Calculate next switch time aligned to clock boundaries like cron.

    For duration=30: switches at :00, :30 of each minute
    For duration=60: switches at :00 of each minute
    For duration=15: switches at :00, :15, :30, :45
    """
    now = _utc_now()
    epoch_seconds = now.timestamp()
    next_boundary = math.ceil(epoch_seconds / duration) * duration
    return datetime.fromtimestamp(next_boundary, tz=timezone.utc)


@dataclass
class DisplayEndpoint:
    """
    Persistent display endpoint that retains state across browser reconnections.

    Pre-created endpoints (is_persistent=True) keep their history forever.
    Auto-generated endpoints (is_persistent=False) are cleaned up after 24h of inactivity.
    """
    endpoint_id: str  # "kitchen-tv" or "anon-abc123"
    name: Optional[str] = None  # Human-readable name for admin UI
    is_persistent: bool = True  # False for auto-generated anonymous endpoints
    current_image: Optional[str] = None
    next_image: Optional[str] = None  # Image being preloaded
    image_history: List[str] = field(default_factory=list)  # Past images for "previous"
    forward_history: List[str] = field(default_factory=list)  # Future images for "next" replay
    created_at: datetime = field(default_factory=_utc_now)
    last_seen: Optional[datetime] = None  # Last time a browser was connected

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses and config persistence."""
        return {
            "endpoint_id": self.endpoint_id,
            "name": self.name,
            "is_persistent": self.is_persistent,
            "current_image": self.current_image,
            "history_count": len(self.image_history),
            "forward_count": len(self.forward_history),
            "image_history": self.image_history[-10:] if self.image_history else [],
            "forward_history": self.forward_history[-10:] if self.forward_history else [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }

    def to_config_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for YAML config persistence (persistent endpoints only).

        Note: History (image_history, forward_history, current_image) is NOT saved because:
        - It's runtime state, not configuration
        - Disk files may change between restarts, making history stale
        - History should start fresh on server restart
        """
        if not self.is_persistent:
            return {}
        return {
            "name": self.name,
        }

    @classmethod
    def from_config_dict(cls, endpoint_id: str, data: Dict[str, Any]) -> "DisplayEndpoint":
        """Create from YAML config dict.

        Note: History is not restored - endpoints start fresh on server restart.
        """
        return cls(
            endpoint_id=endpoint_id,
            name=data.get("name"),
            is_persistent=True,  # Config endpoints are always persistent
            current_image=None,  # Start fresh - no history restored
            image_history=[],
            forward_history=[],
            created_at=_utc_now(),
        )


@dataclass
class BrowserState:
    """Ephemeral state of a connected browser WebSocket connection."""
    browser_id: str  # WebSocket connection ID
    websocket: WebSocket
    endpoint_id: str  # Links to DisplayEndpoint that holds history
    status: BrowserStatus = BrowserStatus.CONNECTING
    connected_at: datetime = field(default_factory=_utc_now)
    last_heartbeat: datetime = field(default_factory=_utc_now)
    evicted: bool = False  # Set by remove_browser; handler checks this to exit


class ImagePool:
    """
    Manages image assignment to ensure unique images per browser
    and no repeats until all images have been shown.
    """

    def __init__(
        self,
        image_service: ImageService,
        settings: DisplayGroupSettings,
        is_folder_locked: Optional[Callable[[str], bool]] = None
    ):
        self.image_service = image_service
        self.settings = settings
        self._is_folder_locked = is_folder_locked or (lambda _: False)
        self._all_images: List[Path] = []
        self._all_paths_cache: Optional[List[str]] = None  # Cached string paths (#26)
        self._shown_images: Set[str] = set()  # Images shown this complete cycle
        self._assigned_images: Dict[str, str] = {}  # browser_id -> image path
        self._lock = asyncio.Lock()  # Lock for atomic assignment (#1)
        self.error: Optional[str] = None  # Error message if folders invalid
        self._refresh_images()

    def has_locked_folders(self) -> List[str]:
        """Return list of configured folders that are currently locked."""
        if not self.settings.folders:
            return []
        folder_paths = [f.strip() for f in self.settings.folders.split(",") if f.strip()]
        return [f for f in folder_paths if self._is_folder_locked(f)]

    def _refresh_images(self) -> None:
        """Reload the list of available images based on settings"""
        self.error = None  # Reset error state
        folder_paths = None
        if self.settings.folders:
            folder_paths = [f.strip() for f in self.settings.folders.split(",") if f.strip()]

            # Filter out locked folders (being updated by Immich pull)
            locked = self.has_locked_folders()
            if locked:
                logger.info(f"Skipping locked folders during refresh: {locked}")
                folder_paths = [f for f in folder_paths if f not in locked]

            # If all folders are locked, keep current images (don't refresh)
            if not folder_paths:
                logger.info("All folders locked, keeping current image pool")
                return

            # Check for missing folders (only check unlocked ones)
            missing = []
            for folder in folder_paths:
                full_path = self.image_service.image_dir / folder.lstrip("/")
                if not full_path.exists():
                    missing.append(folder)
            if missing:
                self.error = f"Folders not found: {', '.join(missing)}"
                logger.error(f"ImagePool error: {self.error}")

        self._all_images = self.image_service.get_image_files(
            folder_paths=folder_paths,
            recursive=self.settings.recursive
        )

        # Track empty pool (no images found, even if folders exist)
        if not self._all_images and not self.error:
            if not self.settings.folders:
                self.error = "No folders configured"
            else:
                self.error = "No images found in configured folders"

        # Invalidate cache when images change (#26)
        self._all_paths_cache = None
        logger.info(f"ImagePool refreshed: {len(self._all_images)} images available")

    def get_all_image_paths(self) -> List[str]:
        """Get all image paths as strings relative to image_dir (cached for performance #26)"""
        if self._all_paths_cache is None:
            self._all_paths_cache = [
                str(img.relative_to(self.image_service.image_dir))
                for img in self._all_images
            ]
        return self._all_paths_cache

    def assign_unique_image(self, browser_id: str) -> Optional[str]:
        """
        Assign a unique image to a browser.

        Returns an image that is:
        1. Not currently assigned to any other browser
        2. Not shown in the current complete cycle (if possible)

        If all images have been shown, resets the cycle.

        IMPORTANT: Caller must hold the ImagePool._lock or DisplayGroup._lock
        to ensure atomic assignment and prevent duplicate images (#1).
        """
        # Handle empty image pool (#28)
        if not self._all_images:
            self._refresh_images()
            if not self._all_images:
                logger.warning("No images available in pool")
                return None

        all_paths = self.get_all_image_paths()

        # Early return if no paths at all (#28)
        if not all_paths:
            logger.warning("No image paths available")
            return None

        # Get currently assigned images (excluding this browser's current)
        currently_assigned = set(self._assigned_images.values())
        if browser_id in self._assigned_images:
            currently_assigned.discard(self._assigned_images[browser_id])

        # Get available images (not assigned and not shown this cycle)
        available = [
            img for img in all_paths
            if img not in currently_assigned and img not in self._shown_images
        ]

        # If no available images, reset the cycle (but keep currently assigned)
        if not available:
            logger.info("All images shown, resetting cycle")
            self._shown_images = currently_assigned.copy()
            available = [img for img in all_paths if img not in currently_assigned]

        # If still no available (fewer images than browsers), allow any unassigned
        if not available:
            available = [img for img in all_paths if img not in currently_assigned]

        # If still nothing available, return any image (more browsers than images)
        if not available:
            logger.warning(f"More browsers than images, reusing images")
            available = all_paths

        # Select random image from available
        selected = random.choice(available)
        self._assigned_images[browser_id] = selected
        self._shown_images.add(selected)
        return selected

    def release_image(self, browser_id: str) -> None:
        """Release an image assignment when browser disconnects"""
        if browser_id in self._assigned_images:
            del self._assigned_images[browser_id]

    def get_assigned_image(self, browser_id: str) -> Optional[str]:
        """Get the currently assigned image for a browser"""
        return self._assigned_images.get(browser_id)

    def update_settings(self, settings: DisplayGroupSettings) -> None:
        """Update settings and refresh image list if folders changed"""
        old_folders = self.settings.folders
        old_recursive = self.settings.recursive
        self.settings = settings

        if settings.folders != old_folders or settings.recursive != old_recursive:
            self._refresh_images()
            # Clear assignments when folders change to prevent stale references (#14)
            self._assigned_images.clear()
            self._shown_images.clear()
            logger.info("Cleared image assignments due to folder change")

    def refresh_images(self) -> int:
        """Refresh the image list from filesystem. Returns count of images found."""
        self._refresh_images()
        # Purge stale entries referencing images that no longer exist
        valid_paths = set(self.get_all_image_paths())
        stale_shown = len(self._shown_images) - len(self._shown_images & valid_paths)
        self._shown_images &= valid_paths
        stale_assigned = {bid for bid, path in self._assigned_images.items() if path not in valid_paths}
        for bid in stale_assigned:
            del self._assigned_images[bid]
        if stale_shown or stale_assigned:
            logger.info(
                f"Purged stale pool state: {stale_shown} shown, "
                f"{len(stale_assigned)} assigned"
            )
        return len(self._all_images)


@dataclass
class DisplayGroup:
    """A synchronized group of browsers displaying images"""
    group_id: str
    settings: DisplayGroupSettings
    image_pool: ImagePool
    browsers: Dict[str, BrowserState] = field(default_factory=dict)
    endpoints: Dict[str, DisplayEndpoint] = field(default_factory=dict)  # Persistent endpoints
    created_at: datetime = field(default_factory=_utc_now)
    current_cycle: int = 0
    next_switch_time: Optional[datetime] = None
    paused: bool = False  # Pause/play state for admin controls
    _cycle_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _webhook_interval_tasks: Dict[str, asyncio.Task] = field(default_factory=dict, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def get_endpoint(self, endpoint_id: str) -> Optional[DisplayEndpoint]:
        """Get an endpoint by ID."""
        return self.endpoints.get(endpoint_id)

    def get_or_create_endpoint(self, endpoint_id: Optional[str] = None) -> DisplayEndpoint:
        """Get existing endpoint or create a new anonymous one."""
        if endpoint_id and endpoint_id in self.endpoints:
            return self.endpoints[endpoint_id]

        # Generate anonymous endpoint ID if not provided
        if not endpoint_id:
            import secrets
            endpoint_id = f"anon-{secrets.token_hex(6)}"

        # Create new endpoint
        is_persistent = not endpoint_id.startswith("anon-")
        endpoint = DisplayEndpoint(
            endpoint_id=endpoint_id,
            is_persistent=is_persistent,
            created_at=_utc_now(),
        )
        self.endpoints[endpoint_id] = endpoint
        return endpoint

    def get_endpoint_for_browser(self, browser_id: str) -> Optional[DisplayEndpoint]:
        """Get the endpoint associated with a browser."""
        browser = self.browsers.get(browser_id)
        if browser:
            return self.endpoints.get(browser.endpoint_id)
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        browsers_list = []
        for b in self.browsers.values():
            # Get endpoint for this browser to access history
            endpoint = self.endpoints.get(b.endpoint_id)
            browsers_list.append({
                "browser_id": b.browser_id,
                "endpoint_id": b.endpoint_id,
                "status": b.status.value,
                "current_image": endpoint.current_image if endpoint else None,
                "connected_at": b.connected_at.isoformat() + "Z",
                # History info from endpoint
                "history_count": len(endpoint.image_history) if endpoint else 0,
                "forward_count": len(endpoint.forward_history) if endpoint else 0,
                "image_history": endpoint.image_history[-10:] if endpoint else [],
                "forward_history": endpoint.forward_history[-10:] if endpoint else [],
            })

        return {
            "group_id": self.group_id,
            "error": self.image_pool.error,  # Legacy field for compatibility
            "image_pool_error": self.image_pool.error,
            "image_count": len(self.image_pool._all_images),
            "browser_count": len(self.browsers),
            "current_cycle": self.current_cycle,
            "paused": self.paused,
            "created_at": self.created_at.isoformat() + "Z",
            "next_switch_at": self.next_switch_time.isoformat() if self.next_switch_time else None,
            "settings": self.settings.to_dict(),
            "browsers": browsers_list,
            "endpoints": [e.to_dict() for e in self.endpoints.values()],
        }


class DisplayManager:
    """
    Manages all display groups and coordinates browser synchronization.
    """

    def __init__(
        self,
        image_service: ImageService,
        config_path: Optional[Path] = None,
        is_folder_locked: Optional[Callable[[str], bool]] = None
    ):
        self.image_service = image_service
        self._is_folder_locked = is_folder_locked or (lambda _: False)
        self.groups: Dict[str, DisplayGroup] = {}
        self._lock = asyncio.Lock()
        self._base_url: str = ""
        # Admin WebSocket connections for real-time updates
        self.admin_connections: Set[WebSocket] = set()
        self._admin_lock = asyncio.Lock()
        # Config file path for persistence
        self.config_path = config_path or Path("config/display_groups.yaml")

    def set_base_url(self, base_url: str) -> None:
        """Set the base URL for image URLs"""
        self._base_url = base_url.rstrip("/")

    async def load_config(self) -> int:
        """Load display groups from YAML config file.

        Returns the number of groups loaded.
        """
        if not self.config_path.exists():
            logger.debug(f"Config file not found: {self.config_path}")
            return 0

        import yaml
        with open(self.config_path, "r") as f:
            config = yaml.safe_load(f) or {}

        loaded = 0
        for group_id, settings_dict in config.get("groups", {}).items():
            try:
                # Extract endpoints before creating settings (they're not part of settings)
                settings_dict = dict(settings_dict)  # Make a copy to avoid modifying original
                endpoints_config = settings_dict.pop("endpoints", {})

                settings = DisplayGroupSettings.from_dict(settings_dict)
                await self.create_group(group_id, settings)

                # Load persistent endpoints
                group = self.groups[group_id]
                for endpoint_id, endpoint_data in endpoints_config.items():
                    endpoint = DisplayEndpoint.from_config_dict(endpoint_id, endpoint_data)
                    group.endpoints[endpoint_id] = endpoint
                    logger.debug(f"Loaded endpoint '{endpoint_id}' for group '{group_id}'")

                endpoint_count = len(endpoints_config)
                if endpoint_count > 0:
                    logger.info(f"Loaded display group from config: {group_id} ({endpoint_count} endpoints)")
                else:
                    logger.info(f"Loaded display group from config: {group_id}")
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load group '{group_id}' from config: {e}")

        return loaded

    def save_config(self) -> int:
        """Save current display groups to YAML config file.

        Returns the number of groups saved.
        """
        import yaml

        config = {"groups": {}}
        for group_id, group in self.groups.items():
            group_config = group.settings.to_config_dict()
            # Add persistent endpoints
            endpoints_config = {}
            for endpoint_id, endpoint in group.endpoints.items():
                if endpoint.is_persistent:
                    endpoint_data = endpoint.to_config_dict()
                    if endpoint_data:  # to_config_dict returns {} for non-persistent
                        endpoints_config[endpoint_id] = endpoint_data
            if endpoints_config:
                group_config["endpoints"] = endpoints_config
            config["groups"][group_id] = group_config

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Saved {len(self.groups)} display groups to config: {self.config_path}")
        return len(self.groups)

    async def register_admin(self, websocket: WebSocket) -> None:
        """Register an admin WebSocket connection for real-time updates."""
        async with self._admin_lock:
            self.admin_connections.add(websocket)
        logger.debug(f"Admin WebSocket registered, total: {len(self.admin_connections)}")

    async def unregister_admin(self, websocket: WebSocket) -> None:
        """Unregister an admin WebSocket connection."""
        async with self._admin_lock:
            self.admin_connections.discard(websocket)
        logger.debug(f"Admin WebSocket unregistered, total: {len(self.admin_connections)}")

    async def _notify_admins(self, message: Dict[str, Any]) -> None:
        """Broadcast an update to all connected admin clients."""
        async with self._admin_lock:
            if not self.admin_connections:
                return
            connections = list(self.admin_connections)

        dead_connections = []
        for ws in connections:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=WEBSOCKET_SEND_TIMEOUT)
            except Exception:
                dead_connections.append(ws)

        # Clean up dead connections
        if dead_connections:
            async with self._admin_lock:
                for ws in dead_connections:
                    self.admin_connections.discard(ws)

    async def _trigger_webhooks(
        self,
        group: DisplayGroup,
        trigger: WebhookTrigger,
        extra_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """Fire webhooks for the given trigger type.

        Runs asynchronously and doesn't block. Errors are logged but don't
        propagate. Should be called outside of locks.
        """
        import hashlib
        import hmac
        import httpx

        webhooks = [
            wh for wh in group.settings.webhooks
            if wh.enabled and trigger in wh.triggers
        ]

        if not webhooks:
            return

        extra_data = extra_data or {}

        for webhook in webhooks:
            try:
                # Build payload based on configured fields
                payload: Dict[str, Any] = {}

                for field in webhook.payload_fields:
                    if field == WebhookPayloadOption.GROUP_ID:
                        payload["group_id"] = group.group_id
                    elif field == WebhookPayloadOption.EVENT_TYPE:
                        payload["event_type"] = trigger.value
                    elif field == WebhookPayloadOption.TIMESTAMP:
                        payload["timestamp"] = _utc_now().isoformat()
                    elif field == WebhookPayloadOption.SETTINGS:
                        payload["settings"] = group.settings.to_dict()
                    elif field == WebhookPayloadOption.CURRENT_IMAGE:
                        # Get current images from all endpoints (via browsers)
                        images = [
                            group.endpoints[b.endpoint_id].current_image
                            for b in group.browsers.values()
                            if b.endpoint_id in group.endpoints and group.endpoints[b.endpoint_id].current_image
                        ]
                        payload["current_images"] = images
                    elif field == WebhookPayloadOption.BROWSER_COUNT:
                        payload["browser_count"] = len(group.browsers)
                    elif field == WebhookPayloadOption.CYCLE_NUMBER:
                        payload["cycle_number"] = group.current_cycle

                # Add any extra data passed in
                payload.update(extra_data)

                # Build headers
                headers = {"Content-Type": "application/json"}
                headers.update(webhook.headers)

                # Add HMAC signature if secret is configured
                if webhook.secret:
                    import json
                    payload_bytes = json.dumps(payload, sort_keys=True).encode()
                    signature = hmac.new(
                        webhook.secret.encode(),
                        payload_bytes,
                        hashlib.sha256
                    ).hexdigest()
                    headers["X-Webhook-Signature"] = f"sha256={signature}"

                # Fire webhook asynchronously (fire and forget)
                async def send_webhook(url: str, payload: dict, headers: dict, webhook_id: str):
                    try:
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            response = await client.post(url, json=payload, headers=headers)
                            if response.status_code >= 400:
                                logger.warning(
                                    f"Webhook {webhook_id} returned {response.status_code}: {url}"
                                )
                            else:
                                logger.debug(f"Webhook {webhook_id} delivered successfully: {url}")
                    except Exception as e:
                        logger.warning(f"Webhook {webhook_id} failed: {e}")

                asyncio.create_task(send_webhook(webhook.url, payload, headers, webhook.id))

            except Exception as e:
                logger.error(f"Error preparing webhook {webhook.id}: {e}")

    def _start_webhook_interval_tasks(self, group: DisplayGroup) -> None:
        """Start interval-based webhook tasks for a group."""
        # Cancel existing interval tasks
        for task in group._webhook_interval_tasks.values():
            if not task.done():
                task.cancel()
        group._webhook_interval_tasks.clear()

        # Start new interval tasks for each webhook with interval trigger
        for webhook in group.settings.webhooks:
            if (
                webhook.enabled
                and WebhookTrigger.INTERVAL in webhook.triggers
                and webhook.interval_minutes
            ):
                task = asyncio.create_task(
                    self._run_webhook_interval(group, webhook)
                )
                group._webhook_interval_tasks[webhook.id] = task
                logger.debug(
                    f"Started interval webhook {webhook.id} for group {group.group_id} "
                    f"every {webhook.interval_minutes} minutes"
                )

    async def _run_webhook_interval(
        self,
        group: DisplayGroup,
        webhook: WebhookConfig
    ) -> None:
        """Run a webhook on a fixed interval."""
        interval_seconds = webhook.interval_minutes * 60

        try:
            while True:
                await asyncio.sleep(interval_seconds)
                # Re-check if webhook is still enabled and has interval trigger
                current_webhook = next(
                    (wh for wh in group.settings.webhooks if wh.id == webhook.id),
                    None
                )
                if not current_webhook or not current_webhook.enabled:
                    break
                if WebhookTrigger.INTERVAL not in current_webhook.triggers:
                    break

                await self._trigger_webhooks(group, WebhookTrigger.INTERVAL)

        except asyncio.CancelledError:
            logger.debug(f"Interval webhook {webhook.id} cancelled for group {group.group_id}")
            raise

    async def create_group(
        self,
        group_id: str,
        settings: Optional[DisplayGroupSettings] = None
    ) -> DisplayGroup:
        """Create a new display group"""
        async with self._lock:
            if group_id in self.groups:
                raise ValueError(f"Group '{group_id}' already exists")

            # Check group limit (A10)
            if len(self.groups) >= MAX_GROUPS:
                raise ValueError(f"Maximum number of groups ({MAX_GROUPS}) reached")

            if settings is None:
                settings = DisplayGroupSettings()

            image_pool = ImagePool(self.image_service, settings, self._is_folder_locked)
            group = DisplayGroup(
                group_id=group_id,
                settings=settings,
                image_pool=image_pool
            )
            self.groups[group_id] = group
            metrics.record_group_created()  # A6
            logger.info(f"Created display group: {group_id}")

        # Notify admin clients (outside lock to avoid deadlock)
        await self._notify_admins({
            "type": "GROUP_CREATED",
            "group": group.to_dict()
        })

        # Start webhook interval tasks if any
        self._start_webhook_interval_tasks(group)

        return group

    async def get_group(self, group_id: str) -> Optional[DisplayGroup]:
        """Get a display group by ID"""
        return self.groups.get(group_id)

    async def delete_group(self, group_id: str) -> bool:
        """Delete a display group and disconnect all browsers"""
        async with self._lock:
            group = self.groups.get(group_id)
            if not group:
                return False

            # Stop the cycle task
            if group._cycle_task and not group._cycle_task.done():
                group._cycle_task.cancel()
                try:
                    await group._cycle_task
                except asyncio.CancelledError:
                    pass

            # Send disconnect message to all browsers
            for browser in list(group.browsers.values()):
                try:
                    await browser.websocket.send_json({
                        "type": "DISCONNECT",
                        "reason": "Group deleted"
                    })
                    await browser.websocket.close()
                except Exception as e:
                    # Log error but continue cleanup (P2)
                    logger.debug(f"Error closing browser {browser.browser_id}: {e}")

            del self.groups[group_id]
            metrics.record_group_deleted()  # A6
            logger.info(f"Deleted display group: {group_id}")

        # Notify admin clients (outside lock to avoid deadlock)
        await self._notify_admins({
            "type": "GROUP_DELETED",
            "group_id": group_id
        })
        return True

    async def update_group_settings(
        self,
        group_id: str,
        updates: Dict[str, Any]
    ) -> Optional[DisplayGroup]:
        """Update settings for a display group and notify all browsers.

        Cancels and restarts the cycle task to apply new settings immediately.
        Uses 3-phase approach to avoid deadlock with _broadcast_to_group.
        """
        group = self.groups.get(group_id)
        if not group:
            return None

        # Phase 1: Update settings and cancel cycle under lock
        async with group._lock:
            current_dict = group.settings.to_dict()
            current_dict.update(updates)
            group.settings = DisplayGroupSettings.from_dict(current_dict)
            group.image_pool.update_settings(group.settings)

            # Cancel existing cycle task to pick up new settings
            if group._cycle_task and not group._cycle_task.done():
                group._cycle_task.cancel()
                try:
                    await group._cycle_task
                except asyncio.CancelledError:
                    pass
                group._cycle_task = None

        # Phase 2: Broadcast OUTSIDE the lock (prevents deadlock)
        message = {
            "type": "SETTINGS_CHANGED",
            "settings": group.settings.to_dict()
        }
        await self._broadcast_to_group(group, message)

        # Phase 3: Restart cycle under lock (only if auto_cycle enabled)
        async with group._lock:
            if not group.paused and group.settings.auto_cycle and group.browsers:
                group._cycle_task = asyncio.create_task(
                    self._run_cycle_loop(group)
                )

        # Log only field names, not values (S11)
        logger.info(f"Updated settings for group {group_id}: {list(updates.keys())}")

        # Notify admin clients
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })

        # Trigger webhooks for settings change
        await self._trigger_webhooks(
            group,
            WebhookTrigger.SETTINGS_CHANGE,
            {"changed_fields": list(updates.keys())}
        )

        # Restart webhook interval tasks if webhooks changed
        if "webhooks" in updates:
            self._start_webhook_interval_tasks(group)

        return group

    async def list_groups(self) -> List[Dict[str, Any]]:
        """List all active display groups"""
        return [group.to_dict() for group in self.groups.values()]

    async def refresh_group_images(self, group_id: str) -> Optional[int]:
        """Refresh image list for a display group. Returns image count or None if group not found."""
        group = self.groups.get(group_id)
        if not group:
            return None
        count = group.image_pool.refresh_images()
        logger.info(f"Refreshed images for group '{group_id}': {count} images")

        # Clean up stale history references (images that no longer exist)
        total_cleaned = 0
        for endpoint in group.endpoints.values():
            cleaned = self._clean_endpoint_history(endpoint)
            total_cleaned += cleaned

        if total_cleaned > 0:
            logger.info(f"Cleaned {total_cleaned} stale history entries in group {group_id}")
            self.save_config()
            await self._notify_admins({"type": "GROUP_UPDATED", "group": group.to_dict()})

        return count

    def get_groups_by_folder(self, folder: str) -> List[str]:
        """Find all group IDs that use a specific folder."""
        matching = []
        for group_id, group in self.groups.items():
            if group.settings.folders:
                folders = [f.strip() for f in group.settings.folders.split(",")]
                if folder in folders or any(folder.startswith(f) or f.startswith(folder) for f in folders):
                    matching.append(group_id)
        return matching

    async def refresh_groups_by_folder(self, folder: str) -> Dict[str, int]:
        """Refresh all groups using a specific folder. Returns {group_id: image_count}."""
        results = {}
        for group_id in self.get_groups_by_folder(folder):
            count = await self.refresh_group_images(group_id)
            if count is not None:
                results[group_id] = count
        return results

    async def add_browser(
        self,
        group_id: str,
        websocket: WebSocket,
        endpoint_id: Optional[str] = None
    ) -> Optional[BrowserState]:
        """
        Add a browser to a display group.

        Args:
            group_id: The display group to join
            websocket: The WebSocket connection
            endpoint_id: Optional endpoint ID for persistent history.
                        If None, an anonymous endpoint is created.

        Returns None if group doesn't exist.
        Raises ValueError if browser limit is reached (A10).
        """
        # Use main lock to prevent race with delete_group (#5, #13)
        async with self._lock:
            group = self.groups.get(group_id)
            if not group:
                return None

            # Check browser limit (A10)
            if len(group.browsers) >= MAX_BROWSERS_PER_GROUP:
                raise ValueError(
                    f"Maximum browsers per group ({MAX_BROWSERS_PER_GROUP}) reached"
                )

            # Get or create the endpoint
            endpoint = group.get_or_create_endpoint(endpoint_id)
            endpoint.last_seen = _utc_now()

            browser_id = uuid.uuid4().hex[:16]  # 64-bit uniqueness (P4)
            browser = BrowserState(
                browser_id=browser_id,
                websocket=websocket,
                endpoint_id=endpoint.endpoint_id,
            )

            async with group._lock:
                group.browsers[browser_id] = browser

                # Assign initial image to endpoint if it doesn't have one
                if not endpoint.current_image:
                    image_path = group.image_pool.assign_unique_image(browser_id)
                    if image_path:
                        endpoint.current_image = image_path

                # Start cycle task if none is running. The cycle loop exits on
                # its own when browsers disconnect, so we just check done/None.
                should_start_task = (
                    not group.paused and
                    group.settings.auto_cycle and
                    (group._cycle_task is None or group._cycle_task.done())
                )
                if should_start_task:
                    group._cycle_task = asyncio.create_task(
                        self._run_cycle_loop(group)
                    )

            metrics.record_connection()  # A6
            logger.info(f"Browser {browser_id} joined group {group_id} (endpoint: {endpoint.endpoint_id})")

        # Notify admin clients (outside lock to avoid deadlock)
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })
        return browser

    async def remove_browser(self, group_id: str, browser_id: str) -> None:
        """Remove a browser from a display group and close its WebSocket.

        Does NOT cancel the cycle task — the cycle loop exits on its own when
        it detects no browsers remain. This avoids self-cancellation bugs when
        remove_browser is called from within the cycle task (via broadcast
        failure or preload failure).
        """
        group = self.groups.get(group_id)
        if not group:
            return

        removed = False
        websocket_to_close = None
        async with group._lock:
            if browser_id in group.browsers:
                browser = group.browsers[browser_id]
                browser.evicted = True  # Signal handler to exit its message loop
                websocket_to_close = browser.websocket
                # Update endpoint's last_seen time
                endpoint = group.endpoints.get(browser.endpoint_id)
                if endpoint:
                    endpoint.last_seen = _utc_now()
                group.image_pool.release_image(browser_id)
                del group.browsers[browser_id]
                metrics.record_disconnection()  # A6
                logger.info(f"Browser {browser_id} left group {group_id}")
                removed = True

        # Close the WebSocket so the client detects the disconnect and reconnects.
        if websocket_to_close:
            try:
                await websocket_to_close.close()
            except Exception:
                pass

        # Notify admin clients (outside lock to avoid deadlock)
        if removed:
            await self._notify_admins({
                "type": "GROUP_UPDATED",
                "group": group.to_dict()
            })

    async def handle_browser_message(
        self,
        group_id: str,
        browser_id: str,
        message: Dict[str, Any]
    ) -> None:
        """Handle a message from a browser"""
        group = self.groups.get(group_id)
        if not group:
            return

        should_notify_admins = False

        # Acquire lock to prevent race with cleanup_stale_browsers (#8)
        async with group._lock:
            browser = group.browsers.get(browser_id)
            if not browser:
                return

            msg_type = message.get("type", "")

            if msg_type == "READY":
                browser.status = BrowserStatus.READY
                logger.debug(f"Browser {browser_id} ready")

            elif msg_type == "PRELOAD_READY":
                browser.status = BrowserStatus.PRELOAD_READY
                logger.debug(f"Browser {browser_id} preload ready")

            elif msg_type == "SWITCH_COMPLETE":
                browser.status = BrowserStatus.READY
                # Move next_image to current_image, push old to history (using endpoint)
                endpoint = group.endpoints.get(browser.endpoint_id)
                if endpoint and endpoint.next_image:
                    # Push current to history before replacing
                    if endpoint.current_image:
                        endpoint.image_history.append(endpoint.current_image)
                    # Move forward items to history (they're already in chronological order)
                    endpoint.image_history.extend(endpoint.forward_history)
                    endpoint.forward_history.clear()
                    endpoint.current_image = endpoint.next_image
                    endpoint.next_image = None
                    # Cap history at MAX_IMAGE_HISTORY
                    while len(endpoint.image_history) > MAX_IMAGE_HISTORY:
                        endpoint.image_history.pop(0)
                    should_notify_admins = True
                logger.debug(f"Browser {browser_id} switch complete")

            elif msg_type == "HEARTBEAT":
                browser.last_heartbeat = _utc_now()

            elif msg_type == "NEXT_IMAGE":
                # Request next image (for on-demand mode)
                # Release lock before calling _advance_browsers which acquires it
                pass  # Handled outside lock below

            elif msg_type == "PREVIOUS_IMAGE":
                # Request previous image (go back in history)
                pass  # Handled outside lock below

            elif msg_type == "IMAGE_LOAD_FAILED":
                # Image failed to load after retries - will assign replacement outside lock
                pass  # Handled outside lock below

        # Handle NEXT_IMAGE/PREVIOUS_IMAGE/IMAGE_LOAD_FAILED outside lock (they acquire their own locks)
        if msg_type == "NEXT_IMAGE":
            await self._advance_browsers(group, [browser_id])
            should_notify_admins = True
        elif msg_type == "PREVIOUS_IMAGE":
            await self._rewind_browsers(group, [browser_id])
            should_notify_admins = True
        elif msg_type == "IMAGE_LOAD_FAILED":
            failed_path = message.get("image_path")
            if failed_path:
                logger.warning(f"Browser {browser_id} failed to load image: {failed_path}")
                await self._assign_replacement_image(group, browser_id, failed_path)
                should_notify_admins = True

        # Notify admin clients outside lock
        if should_notify_admins:
            await self._notify_admins({
                "type": "GROUP_UPDATED",
                "group": group.to_dict()
            })

    def build_image_url(self, image_path: str, settings: DisplayGroupSettings) -> str:
        """Build a full image URL with processing parameters"""
        params = []

        if settings.width:
            params.append(f"width={settings.width}")
        if settings.height:
            params.append(f"height={settings.height}")
        if not settings.preserve_aspect:
            params.append("preserve_aspect=false")
        if settings.mode != "fit":
            params.append(f"mode={settings.mode}")
        if settings.pad_color != "black":
            params.append(f"pad_color={settings.pad_color}")
        if settings.eink:
            params.append("eink=true")
            # Only include E-Ink params if different from defaults
            if settings.saturation != 2.5:
                params.append(f"saturation={settings.saturation}")
            if settings.contrast != 1.2:
                params.append(f"contrast={settings.contrast}")
            if settings.brightness != 1.1:
                params.append(f"brightness={settings.brightness}")
            if not settings.sharpen:
                params.append("sharpen=false")
        if settings.format:
            params.append(f"format={settings.format}")

        url = f"{self._base_url}/api/image/{image_path}"
        if params:
            url += "?" + "&".join(params)
        return url

    def build_blur_url(self, image_path: str, settings: DisplayGroupSettings) -> Optional[str]:
        """Generate blur data URL for image if blur is enabled.

        Returns a base64 data URL of the blurred image, or None if blur is disabled
        or generation fails.
        """
        if not settings.background_blur or settings.image_fit == 'cover':
            return None

        try:
            full_path = resolve_image_path(image_path, self.image_service.image_dir)
            img = self.image_service.load_image(full_path)
            return create_blur_background(img, settings.background_blur_amount)
        except Exception as e:
            logger.warning(f"Failed to generate blur for {image_path}: {e}")
            return None

    async def _send_to_browser(
        self,
        browser_id: str,
        browser: BrowserState,
        message: Dict[str, Any],
        timeout: float = 5.0
    ) -> Optional[str]:
        """Send message to a single browser, return browser_id if failed."""
        try:
            await asyncio.wait_for(
                browser.websocket.send_json(message),
                timeout=timeout
            )
            return None  # Success
        except asyncio.TimeoutError:
            logger.warning(f"Send timeout to browser {browser_id}")
            return browser_id
        except Exception as e:
            logger.warning(f"Failed to send to browser {browser_id}: {e}")
            return browser_id

    async def _broadcast_to_group(
        self,
        group: DisplayGroup,
        message: Dict[str, Any]
    ) -> None:
        """Send a message to all browsers in a group (A2 - parallel sends)."""
        # Take snapshot of browsers to avoid concurrent modification (#10)
        async with group._lock:
            browsers_snapshot = list(group.browsers.items())

        if not browsers_snapshot:
            return

        # Send to all browsers in parallel (A2)
        results = await asyncio.gather(
            *[
                self._send_to_browser(browser_id, browser, message)
                for browser_id, browser in browsers_snapshot
            ],
            return_exceptions=True
        )

        # Collect disconnected browsers
        disconnected = [
            browser_id for browser_id, result in zip(
                [b[0] for b in browsers_snapshot], results
            )
            if result is not None and not isinstance(result, Exception)
        ]

        # Clean up disconnected browsers
        for browser_id in disconnected:
            await self.remove_browser(group.group_id, browser_id)

    async def _run_cycle_loop(self, group: DisplayGroup) -> None:
        """Main loop that coordinates image cycling for a group with cron-aligned timing.

        Images switch at clock-aligned boundaries (like cron):
        - duration=30: switches at :00, :30 of each minute
        - duration=60: switches at :00 of each minute
        - duration=15: switches at :00, :15, :30, :45
        """
        logger.info(f"Starting cycle loop for group {group.group_id}")

        try:
            while True:
                try:
                    # Exit when no browsers are connected — the task will be
                    # restarted by add_browser when the next browser joins.
                    if not group.browsers:
                        logger.info(f"No browsers in {group.group_id}, exiting cycle loop")
                        break

                    duration = group.settings.duration

                    # Check if all folders are locked (being updated by Immich pull)
                    locked_folders = group.image_pool.has_locked_folders()
                    if locked_folders and group.settings.folders:
                        all_folders = [f.strip() for f in group.settings.folders.split(",") if f.strip()]
                        if set(locked_folders) >= set(all_folders):
                            # All configured folders are locked, skip this cycle
                            logger.debug(f"Group {group.group_id}: skipping cycle, all folders locked ({locked_folders})")
                            await asyncio.sleep(1)  # Brief pause before checking again
                            continue

                    # Calculate next cron-aligned switch time
                    next_switch = _next_cron_time(duration)
                    group.next_switch_time = next_switch

                    # Notify admin clients of new countdown time
                    await self._notify_admins({
                        "type": "GROUP_UPDATED",
                        "group": group.to_dict()
                    })

                    # Wait until preload time (PRELOAD_LEAD_TIME_SECONDS before switch)
                    preload_time = next_switch - timedelta(seconds=PRELOAD_LEAD_TIME_SECONDS)
                    now = _utc_now()
                    preload_delay = (preload_time - now).total_seconds()
                    if preload_delay > 0:
                        await asyncio.sleep(preload_delay)

                    # Prepare next images for all browsers
                    switch_at = next_switch.timestamp() * 1000
                    async with group._lock:
                        # Take snapshot for iteration and cache settings (#25)
                        browsers_to_update = list(group.browsers.values())
                        settings = group.settings

                    preload_failed = []
                    for browser in browsers_to_update:
                        async with group._lock:
                            # Assign under lock for atomicity (#1)
                            endpoint = group.endpoints.get(browser.endpoint_id)
                            next_image = group.image_pool.assign_unique_image(browser.browser_id)
                            if next_image and endpoint:
                                endpoint.next_image = next_image
                                browser.status = BrowserStatus.PRELOADING

                        if next_image and endpoint:
                            # Build URL once per image (#25 - partially, each browser gets unique image)
                            image_url = self.build_image_url(next_image, settings)
                            blur_url = self.build_blur_url(next_image, settings)
                            try:
                                await asyncio.wait_for(
                                    browser.websocket.send_json({
                                        "type": "PRELOAD",
                                        "image_url": image_url,
                                        "blur_url": blur_url,
                                        "image_id": next_image,
                                        "switch_at": switch_at
                                    }),
                                    timeout=WEBSOCKET_SEND_TIMEOUT
                                )
                            except asyncio.TimeoutError:
                                logger.warning(f"Preload send timeout for {browser.browser_id}")
                                preload_failed.append(browser.browser_id)
                            except Exception as e:
                                logger.warning(f"Failed to send preload to {browser.browser_id}: {e}")
                                preload_failed.append(browser.browser_id)

                    # Remove browsers where preload send failed — connection is likely dead.
                    # Without this, dead browsers linger until the SWITCH broadcast
                    # also fails, leaving stale next_image on the endpoint.
                    for browser_id in preload_failed:
                        await self.remove_browser(group.group_id, browser_id)

                    # If all browsers gone after preload removal, skip SWITCH
                    # and loop back to the exit check at the top.
                    if not group.browsers:
                        continue

                    # Wait until exact switch time
                    now = _utc_now()
                    switch_delay = (next_switch - now).total_seconds()
                    if switch_delay > 0:
                        await asyncio.sleep(switch_delay)

                    # Send synchronized switch command (S14 - wrap to prevent overflow)
                    group.current_cycle = (group.current_cycle + 1) % MAX_CYCLE_COUNT
                    metrics.record_cycle()  # A6
                    switch_timestamp = _utc_now().timestamp() * 1000

                    await self._broadcast_to_group(group, {
                        "type": "SWITCH",
                        "timestamp": switch_timestamp,
                        "cycle": group.current_cycle
                    })

                    # Trigger webhooks for image cycle
                    await self._trigger_webhooks(group, WebhookTrigger.IMAGE_CYCLE)

                    logger.debug(f"Group {group.group_id} cycle {group.current_cycle} switch at {next_switch.isoformat()}")

                except asyncio.CancelledError:
                    raise  # Re-raise cancellation
                except Exception as e:
                    # Catch all other exceptions and continue (#3)
                    metrics.record_error()  # A6
                    logger.error(
                        f"Error in cycle loop for {group.group_id}: {e} "
                        f"(browsers={len(group.browsers)}, "
                        f"images={len(group.image_pool._all_images)}, "
                        f"pool_error={group.image_pool.error})",
                        exc_info=True
                    )
                    await asyncio.sleep(1)  # Brief pause before retry

        except asyncio.CancelledError:
            logger.info(f"Cycle loop cancelled for group {group.group_id}")
            raise

    async def cleanup_stale_browsers(self, timeout_seconds: int = 60) -> None:
        """Remove browsers that haven't sent a heartbeat within timeout (A7)."""
        now = _utc_now()
        # Take snapshot of groups to avoid modification during iteration
        groups_snapshot = list(self.groups.values())

        for group in groups_snapshot:
            stale = []
            # Acquire lock before iterating browsers (A7)
            async with group._lock:
                for browser_id, browser in group.browsers.items():
                    elapsed = (now - browser.last_heartbeat).total_seconds()
                    if elapsed > timeout_seconds:
                        stale.append(browser_id)

            for browser_id in stale:
                logger.info(f"Removing stale browser {browser_id}")
                await self.remove_browser(group.group_id, browser_id)

    async def cleanup_stale_endpoints(self, timeout_seconds: int = 86400) -> int:
        """
        Remove anonymous endpoints that haven't been connected for timeout_seconds.

        Only removes endpoints where is_persistent=False and no browser is connected.
        Returns the number of endpoints cleaned up.
        """
        now = _utc_now()
        cleaned_count = 0

        # Take snapshot of groups to avoid modification during iteration
        groups_snapshot = list(self.groups.values())

        for group in groups_snapshot:
            stale_endpoints = []

            async with group._lock:
                # Find connected endpoint IDs
                connected_endpoint_ids = {b.endpoint_id for b in group.browsers.values()}

                for endpoint_id, endpoint in group.endpoints.items():
                    # Skip persistent endpoints
                    if endpoint.is_persistent:
                        continue
                    # Skip endpoints with connected browsers
                    if endpoint_id in connected_endpoint_ids:
                        continue
                    # Check if last_seen is beyond timeout
                    if endpoint.last_seen:
                        elapsed = (now - endpoint.last_seen).total_seconds()
                        if elapsed > timeout_seconds:
                            stale_endpoints.append(endpoint_id)

                # Remove stale endpoints
                for endpoint_id in stale_endpoints:
                    del group.endpoints[endpoint_id]
                    cleaned_count += 1
                    logger.info(
                        f"Cleaned up stale anonymous endpoint {endpoint_id} "
                        f"from group {group.group_id}"
                    )

        if cleaned_count > 0:
            # Save config after cleanup
            self.save_config()

        return cleaned_count

    async def health_check_groups(self) -> int:
        """Check all groups for degraded state and attempt recovery.

        Recovers groups with:
        - Empty/errored image pools that may have become available
        - Dead cycle tasks that should be running

        Returns number of groups that needed recovery action.
        """
        recovered = 0
        groups_snapshot = list(self.groups.values())

        for group in groups_snapshot:
            actions = []

            # Check 1: Image pool has error but images might now be available
            if group.image_pool.error and group.browsers:
                old_error = group.image_pool.error
                group.image_pool.refresh_images()
                if not group.image_pool.error:
                    actions.append(f"image pool recovered from: {old_error}")

            # Check 2: Cycle task should be running but isn't
            should_have_task = (
                group.browsers
                and not group.paused
                and group.settings.auto_cycle
            )
            task_dead = (
                group._cycle_task is None
                or group._cycle_task.done()
            )
            if should_have_task and task_dead:
                async with group._lock:
                    # Re-check under lock to avoid race
                    if (group.browsers and not group.paused
                            and group.settings.auto_cycle
                            and (group._cycle_task is None or group._cycle_task.done())):
                        group._cycle_task = asyncio.create_task(
                            self._run_cycle_loop(group)
                        )
                        actions.append("restarted dead cycle task")

            if actions:
                recovered += 1
                logger.warning(
                    f"Health check for group '{group.group_id}': {', '.join(actions)}"
                )

        return recovered

    # --- Playback Control Methods ---

    async def pause_group(self, group_id: str) -> bool:
        """Pause cycling for a group."""
        group = self.groups.get(group_id)
        if not group:
            return False

        async with group._lock:
            if group.paused:
                return True  # Already paused

            group.paused = True
            # Cancel the cycle task if running
            if group._cycle_task and not group._cycle_task.done():
                group._cycle_task.cancel()
                try:
                    await group._cycle_task
                except asyncio.CancelledError:
                    pass
            group._cycle_task = None
            logger.info(f"Group {group_id} paused")

        # Notify admin clients
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })
        return True

    async def play_group(self, group_id: str) -> bool:
        """Resume cycling for a group."""
        group = self.groups.get(group_id)
        if not group:
            return False

        async with group._lock:
            if not group.paused:
                return True  # Already playing

            group.paused = False
            # Restart the cycle task if there are browsers and auto_cycle is enabled
            if len(group.browsers) > 0 and group.settings.auto_cycle:
                group._cycle_task = asyncio.create_task(
                    self._run_cycle_loop(group)
                )
            logger.info(f"Group {group_id} resumed")

        # Notify admin clients
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })
        return True

    async def skip_group(self, group_id: str) -> bool:
        """Skip to next image immediately.

        If any browser has forward_history (from going back), replay those first.
        Otherwise fetch new images from pool.
        """
        group = self.groups.get(group_id)
        if not group or not group.browsers:
            return False

        async with group._lock:
            # Cancel current wait
            if group._cycle_task and not group._cycle_task.done():
                group._cycle_task.cancel()
                try:
                    await group._cycle_task
                except asyncio.CancelledError:
                    pass

        # Check if any endpoint has forward_history to replay
        can_replay = any(
            len(group.endpoints.get(b.endpoint_id, DisplayEndpoint("")).forward_history) > 0
            for b in group.browsers.values()
        )

        if can_replay:
            # Replay from forward_history
            await self._forward_browsers(group)
        else:
            # Fetch new images from pool
            await self._advance_browsers(group)

        async with group._lock:
            # Restart cycle task if not paused and auto_cycle enabled
            if not group.paused and group.settings.auto_cycle and group.browsers:
                group._cycle_task = asyncio.create_task(
                    self._run_cycle_loop(group)
                )

        logger.info(f"Group {group_id} skipped to next")

        # Notify admin clients
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })
        return True

    async def previous_group(self, group_id: str) -> bool:
        """Go to previous image - each browser goes to its own previous image."""
        group = self.groups.get(group_id)
        if not group or not group.browsers:
            logger.info(f"previous_group({group_id}): no group or no browsers")
            return False

        # Debug: log state of all browsers/endpoints before rewind
        for bid, b in group.browsers.items():
            endpoint = group.endpoints.get(b.endpoint_id)
            if endpoint:
                logger.info(f"previous_group({group_id}): browser {bid}: "
                           f"history_len={len(endpoint.image_history)}, "
                           f"current={endpoint.current_image}, next={endpoint.next_image}")

        # Check if any endpoint can rewind (has history OR is mid-switch with next_image)
        can_rewind = any(
            (ep := group.endpoints.get(browser.endpoint_id)) and
            (len(ep.image_history) > 0 or ep.next_image is not None)
            for browser in group.browsers.values()
        )
        if not can_rewind:
            logger.info(f"previous_group({group_id}): can_rewind=False, returning")
            return False

        async with group._lock:
            # Cancel current cycle task
            if group._cycle_task and not group._cycle_task.done():
                group._cycle_task.cancel()
                try:
                    await group._cycle_task
                except asyncio.CancelledError:
                    pass

        # Rewind each browser (outside lock to allow message sending)
        await self._rewind_browsers(group)

        async with group._lock:
            # Restart cycle task if not paused and auto_cycle enabled
            if not group.paused and group.settings.auto_cycle and group.browsers:
                group._cycle_task = asyncio.create_task(
                    self._run_cycle_loop(group)
                )

        logger.info(f"Group {group_id} went to previous")

        # Notify admin clients
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })
        return True

    async def rewind_all_group(self, group_id: str) -> bool:
        """Jump to first image in history (bulk operation, single update)."""
        group = self.groups.get(group_id)
        if not group or not group.browsers:
            return False

        # Cancel cycling during operation
        async with group._lock:
            if group._cycle_task and not group._cycle_task.done():
                group._cycle_task.cancel()
                try:
                    await group._cycle_task
                except asyncio.CancelledError:
                    pass

        browsers_to_assign = []
        async with group._lock:
            for browser in group.browsers.values():
                endpoint = group.endpoints.get(browser.endpoint_id)
                if not endpoint or not endpoint.image_history:
                    continue

                # Push current to forward_history
                if endpoint.current_image:
                    endpoint.forward_history.append(endpoint.current_image)
                # Push remaining history items to forward (reversed to preserve order)
                endpoint.forward_history.extend(reversed(endpoint.image_history[1:]))

                # First item in history becomes current
                first_image = endpoint.image_history[0]
                endpoint.current_image = first_image
                endpoint.image_history.clear()
                browser.status = BrowserStatus.READY

                browsers_to_assign.append((browser, first_image))

        # Send ASSIGNs
        settings = group.settings
        for browser, image_path in browsers_to_assign:
            image_url = self.build_image_url(image_path, settings)
            blur_url = self.build_blur_url(image_path, settings)
            try:
                await asyncio.wait_for(
                    browser.websocket.send_json({
                        "type": "ASSIGN",
                        "image_url": image_url,
                        "blur_url": blur_url,
                        "image_id": image_path,
                        "cycle": group.current_cycle
                    }),
                    timeout=WEBSOCKET_SEND_TIMEOUT
                )
            except Exception as e:
                logger.warning(f"Failed to send assign to {browser.browser_id}: {e}")

        # Restart cycle if not paused and auto_cycle enabled
        async with group._lock:
            if not group.paused and group.settings.auto_cycle and group.browsers:
                group._cycle_task = asyncio.create_task(self._run_cycle_loop(group))

        logger.info(f"Group {group_id} rewound to first image")

        # Notify admin clients
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })
        return True

    async def forward_all_group(self, group_id: str) -> bool:
        """Jump to most recent image (bulk operation, single update)."""
        group = self.groups.get(group_id)
        if not group or not group.browsers:
            return False

        # Cancel cycling during operation
        async with group._lock:
            if group._cycle_task and not group._cycle_task.done():
                group._cycle_task.cancel()
                try:
                    await group._cycle_task
                except asyncio.CancelledError:
                    pass

        browsers_to_assign = []
        async with group._lock:
            for browser in group.browsers.values():
                endpoint = group.endpoints.get(browser.endpoint_id)
                if not endpoint or not endpoint.forward_history:
                    continue

                # Push current to history
                if endpoint.current_image:
                    endpoint.image_history.append(endpoint.current_image)
                # Push all forward items except last to history
                endpoint.image_history.extend(endpoint.forward_history[:-1])

                # Last item in forward_history becomes current
                last_image = endpoint.forward_history[-1]
                endpoint.current_image = last_image
                endpoint.forward_history.clear()
                browser.status = BrowserStatus.READY

                # Trim history if needed
                while len(endpoint.image_history) > MAX_IMAGE_HISTORY:
                    endpoint.image_history.pop(0)

                browsers_to_assign.append((browser, last_image))

        # Send ASSIGNs
        settings = group.settings
        for browser, image_path in browsers_to_assign:
            image_url = self.build_image_url(image_path, settings)
            blur_url = self.build_blur_url(image_path, settings)
            try:
                await asyncio.wait_for(
                    browser.websocket.send_json({
                        "type": "ASSIGN",
                        "image_url": image_url,
                        "blur_url": blur_url,
                        "image_id": image_path,
                        "cycle": group.current_cycle
                    }),
                    timeout=WEBSOCKET_SEND_TIMEOUT
                )
            except Exception as e:
                logger.warning(f"Failed to send assign to {browser.browser_id}: {e}")

        # Restart cycle if not paused and auto_cycle enabled
        async with group._lock:
            if not group.paused and group.settings.auto_cycle and group.browsers:
                group._cycle_task = asyncio.create_task(self._run_cycle_loop(group))

        logger.info(f"Group {group_id} forwarded to most recent image")

        # Notify admin clients
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })
        return True

    async def jump_to_image(
        self, group_id: str, browser_id: str, image_path: str
    ) -> bool:
        """Jump a specific browser to a specific image from its history."""
        group = self.groups.get(group_id)
        if not group:
            return False

        browser = group.browsers.get(browser_id)
        if not browser:
            return False

        endpoint = group.endpoints.get(browser.endpoint_id)
        if not endpoint:
            return False

        # Validate the target image still exists on disk
        if not self._validate_image_path(image_path):
            logger.warning(f"Jump target no longer exists: {image_path}")
            # Clean up stale history entries
            self._clean_endpoint_history(endpoint)
            return False

        # Cancel current cycle task
        async with group._lock:
            if group._cycle_task and not group._cycle_task.done():
                group._cycle_task.cancel()
                try:
                    await group._cycle_task
                except asyncio.CancelledError:
                    pass

        # Find image in history or forward_history (using endpoint)
        async with group._lock:
            # Check if jumping to past image
            if image_path in endpoint.image_history:
                idx = endpoint.image_history.index(image_path)
                # Build new forward: history items after idx, then current, then existing forward
                new_forward = []
                for img in endpoint.image_history[idx + 1:]:
                    new_forward.append(img)
                if endpoint.current_image:
                    new_forward.append(endpoint.current_image)
                # Prepend to existing forward (existing items are newer)
                new_forward.extend(endpoint.forward_history)
                endpoint.forward_history = new_forward
                # Set new current and trim history
                endpoint.current_image = image_path
                endpoint.image_history = endpoint.image_history[:idx]

            # Check if jumping to forward image
            elif image_path in endpoint.forward_history:
                idx = endpoint.forward_history.index(image_path)
                # forward_history stores [oldest, ..., newest]
                # Items BEFORE idx are older - add current then those to history
                if endpoint.current_image:
                    endpoint.image_history.append(endpoint.current_image)
                for img in endpoint.forward_history[:idx]:
                    endpoint.image_history.append(img)
                # Items AFTER idx are newer - stay as forward
                endpoint.forward_history = endpoint.forward_history[idx + 1:]
                endpoint.current_image = image_path
                # Trim history if needed
                while len(endpoint.image_history) > MAX_IMAGE_HISTORY:
                    endpoint.image_history.pop(0)
            else:
                # Image not in history - can't jump
                return False

            endpoint.next_image = None
            browser.status = BrowserStatus.READY

        # Send ASSIGN
        image_url = self.build_image_url(image_path, group.settings)
        blur_url = self.build_blur_url(image_path, group.settings)
        try:
            await asyncio.wait_for(
                browser.websocket.send_json({
                    "type": "ASSIGN",
                    "image_url": image_url,
                    "blur_url": blur_url,
                    "image_id": image_path,
                    "cycle": group.current_cycle
                }),
                timeout=WEBSOCKET_SEND_TIMEOUT
            )
        except Exception as e:
            logger.warning(f"Failed to send jump assign to {browser_id}: {e}")
            return False

        # Restart cycle if not paused and auto_cycle enabled
        async with group._lock:
            if not group.paused and group.settings.auto_cycle and group.browsers:
                group._cycle_task = asyncio.create_task(
                    self._run_cycle_loop(group)
                )

        logger.info(f"Browser {browser_id} in group {group_id} jumped to {image_path}")

        # Notify admin clients
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })

        return True

    async def jump_endpoint_to_image(
        self, group_id: str, endpoint_id: str, image_path: str
    ) -> bool:
        """Jump an endpoint to a specific image from its history (no browser required).

        Works for disconnected endpoints where no browser WebSocket is connected.
        The image must be in the endpoint's image_history or forward_history.
        """
        group = self.groups.get(group_id)
        if not group:
            return False

        endpoint = group.endpoints.get(endpoint_id)
        if not endpoint:
            return False

        # Validate the target image still exists on disk
        if not self._validate_image_path(image_path):
            logger.warning(f"Jump target no longer exists: {image_path}")
            self._clean_endpoint_history(endpoint)
            return False

        # Find image in history or forward_history
        if image_path in endpoint.image_history:
            idx = endpoint.image_history.index(image_path)
            # Build new forward: history items after idx, then current, then existing forward
            new_forward = []
            for img in endpoint.image_history[idx + 1:]:
                new_forward.append(img)
            if endpoint.current_image:
                new_forward.append(endpoint.current_image)
            new_forward.extend(endpoint.forward_history)
            endpoint.forward_history = new_forward
            endpoint.current_image = image_path
            endpoint.image_history = endpoint.image_history[:idx]

        elif image_path in endpoint.forward_history:
            idx = endpoint.forward_history.index(image_path)
            # Push current and items before idx to history
            if endpoint.current_image:
                endpoint.image_history.append(endpoint.current_image)
            for img in endpoint.forward_history[:idx]:
                endpoint.image_history.append(img)
            endpoint.forward_history = endpoint.forward_history[idx + 1:]
            endpoint.current_image = image_path
            # Trim history if needed
            while len(endpoint.image_history) > MAX_IMAGE_HISTORY:
                endpoint.image_history.pop(0)
        else:
            # Image not in history - can't jump
            return False

        endpoint.next_image = None

        logger.info(f"Endpoint {endpoint_id} in group {group_id} jumped to {image_path}")

        # Notify admin clients
        await self._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })

        return True

    async def _advance_browsers(
        self, group: DisplayGroup, browser_ids: Optional[List[str]] = None
    ) -> None:
        """Immediately advance browsers to next image.

        Args:
            group: The display group
            browser_ids: Optional list of browser IDs to advance. If None, advances all.
        """
        settings = group.settings
        browsers_to_assign = []

        async with group._lock:
            browsers = list(group.browsers.values())
            if browser_ids is not None:
                browsers = [b for b in browsers if b.browser_id in browser_ids]
            for browser in browsers:
                endpoint = group.endpoints.get(browser.endpoint_id)
                if not endpoint:
                    continue

                next_image = group.image_pool.assign_unique_image(browser.browser_id)
                if not next_image:
                    continue

                # Push current to history (using endpoint)
                if endpoint.current_image:
                    endpoint.image_history.append(endpoint.current_image)
                    if len(endpoint.image_history) > MAX_IMAGE_HISTORY:
                        endpoint.image_history.pop(0)

                endpoint.current_image = next_image
                endpoint.next_image = None
                browser.status = BrowserStatus.READY
                browsers_to_assign.append((browser, next_image))

        # Increment cycle
        group.current_cycle = (group.current_cycle + 1) % MAX_CYCLE_COUNT
        metrics.record_cycle()

        # Send ASSIGNs (blur applied immediately on client)
        for browser, image_path in browsers_to_assign:
            image_url = self.build_image_url(image_path, settings)
            blur_url = self.build_blur_url(image_path, settings)
            try:
                await asyncio.wait_for(
                    browser.websocket.send_json({
                        "type": "ASSIGN",
                        "image_url": image_url,
                        "blur_url": blur_url,
                        "image_id": image_path,
                        "cycle": group.current_cycle
                    }),
                    timeout=WEBSOCKET_SEND_TIMEOUT
                )
            except Exception as e:
                logger.warning(f"Failed to send assign to {browser.browser_id}: {e}")

    async def _forward_browsers(self, group: DisplayGroup) -> None:
        """Move forward through forward_history (replay previously seen images)."""
        settings = group.settings
        browsers_to_assign = []

        async with group._lock:
            for browser in list(group.browsers.values()):
                endpoint = group.endpoints.get(browser.endpoint_id)
                if not endpoint or not endpoint.forward_history:
                    continue

                # Push current to history
                if endpoint.current_image:
                    endpoint.image_history.append(endpoint.current_image)
                    if len(endpoint.image_history) > MAX_IMAGE_HISTORY:
                        endpoint.image_history.pop(0)

                # Pop from forward_history
                next_image = endpoint.forward_history.pop()
                endpoint.current_image = next_image
                browser.status = BrowserStatus.READY

                browsers_to_assign.append((browser, next_image))

        # Send ASSIGNs outside the lock
        for browser, image_path in browsers_to_assign:
            image_url = self.build_image_url(image_path, settings)
            blur_url = self.build_blur_url(image_path, settings)
            try:
                await asyncio.wait_for(
                    browser.websocket.send_json({
                        "type": "ASSIGN",
                        "image_url": image_url,
                        "blur_url": blur_url,
                        "image_id": image_path,
                        "cycle": group.current_cycle
                    }),
                    timeout=WEBSOCKET_SEND_TIMEOUT
                )
            except Exception as e:
                logger.warning(f"Failed to send assign to {browser.browser_id}: {e}")

    async def _rewind_browsers(
        self, group: DisplayGroup, browser_ids: Optional[List[str]] = None
    ) -> None:
        """Rewind browsers to their previous images.

        Args:
            group: The display group
            browser_ids: Optional list of browser IDs to rewind. If None, rewinds all.
        """
        settings = group.settings

        # Collect what to send while holding the lock to prevent race with SWITCH_COMPLETE
        browsers_to_assign = []

        async with group._lock:
            browsers = list(group.browsers.values())
            if browser_ids is not None:
                browsers = [b for b in browsers if b.browser_id in browser_ids]
            for browser in browsers:
                endpoint = group.endpoints.get(browser.endpoint_id)
                if not endpoint:
                    continue

                previous_image = None
                rewind_case = None

                logger.info(f"_rewind_browsers: browser {browser.browser_id}: "
                           f"BEFORE history={[p.split('/')[-1] for p in endpoint.image_history]}, "
                           f"current={endpoint.current_image}, next={endpoint.next_image}")

                # Case 1: Browser is mid-switch (next_image is set, SWITCH_COMPLETE not yet received)
                # The browser is showing/transitioning to next_image, cancel and stay on current_image
                if endpoint.next_image:
                    previous_image = endpoint.current_image
                    rewind_case = "mid-switch"
                    # Clear next_image to cancel the pending switch
                    endpoint.next_image = None
                    # Note: Don't pop from history - we're just canceling a pending switch
                # Case 2: Normal rewind from history
                elif endpoint.image_history:
                    # Push current to forward_history (for Next to replay)
                    if endpoint.current_image:
                        endpoint.forward_history.append(endpoint.current_image)
                        group.image_pool.release_image(browser.browser_id)
                    previous_image = endpoint.image_history.pop()
                    rewind_case = "history"
                    endpoint.current_image = previous_image
                else:
                    # No next_image and no history - nothing to rewind to
                    logger.info(f"_rewind_browsers: browser {browser.browser_id}: SKIP (no next, no history)")
                    continue

                browser.status = BrowserStatus.READY

                logger.info(f"_rewind_browsers: browser {browser.browser_id}: "
                           f"case={rewind_case}, will send ASSIGN {previous_image}")

                if previous_image:
                    browsers_to_assign.append((browser, previous_image))

        # Send ASSIGNs outside the lock to avoid holding it during network I/O
        for browser, image_path in browsers_to_assign:
            image_url = self.build_image_url(image_path, settings)
            blur_url = self.build_blur_url(image_path, settings)
            try:
                await asyncio.wait_for(
                    browser.websocket.send_json({
                        "type": "ASSIGN",
                        "image_url": image_url,
                        "blur_url": blur_url,
                        "image_id": image_path,
                        "cycle": group.current_cycle
                    }),
                    timeout=WEBSOCKET_SEND_TIMEOUT
                )
                logger.info(f"_rewind_browsers: browser {browser.browser_id}: ASSIGN sent successfully")
            except Exception as e:
                logger.warning(f"Failed to send assign to {browser.browser_id}: {e}")

    async def _assign_replacement_image(
        self, group: DisplayGroup, browser_id: str, failed_path: str
    ) -> bool:
        """
        Assign a replacement image when one fails to load.

        Called when browser reports IMAGE_LOAD_FAILED after exhausting retries.
        Immediately assigns a new image so display doesn't stay blank/stuck.
        """
        browser = group.browsers.get(browser_id)
        if not browser:
            return False

        endpoint = group.endpoints.get(browser.endpoint_id)
        if not endpoint:
            return False

        async with group._lock:
            # Release the failed image from pool assignment
            if group.image_pool._assigned_images.get(browser_id) == failed_path:
                group.image_pool.release_image(browser_id)

            # Get new image
            new_image = group.image_pool.assign_unique_image(browser_id)
            if not new_image:
                logger.warning(f"No replacement image available for {browser_id}")
                return False

            # Update endpoint state
            endpoint.current_image = new_image
            endpoint.next_image = None
            browser.status = BrowserStatus.READY

        # Send replacement assignment
        image_url = self.build_image_url(new_image, group.settings)
        blur_url = self.build_blur_url(new_image, group.settings)

        try:
            await asyncio.wait_for(
                browser.websocket.send_json({
                    "type": "ASSIGN",
                    "image_url": image_url,
                    "blur_url": blur_url,
                    "image_id": new_image,
                    "cycle": group.current_cycle
                }),
                timeout=WEBSOCKET_SEND_TIMEOUT
            )
            logger.info(f"Sent replacement image {new_image} to {browser_id} (failed: {failed_path})")
            return True
        except Exception as e:
            logger.warning(f"Failed to send replacement to {browser_id}: {e}")
            return False

    def advance_endpoint_no_browser(
        self, group: DisplayGroup, endpoint: DisplayEndpoint
    ) -> Optional[str]:
        """
        Advance endpoint to next image when no browser is connected.

        Used by the /display?action=next URL and API endpoints.
        Returns the new current_image path, or None if no images available.
        """
        # Get folder paths from settings
        folder_paths = None
        if group.settings.folders:
            folder_paths = [f.strip() for f in group.settings.folders.split(",") if f.strip()]

        # get_image_files returns List[Path]
        image_paths = self.image_service.get_image_files(
            folder_paths=folder_paths,
            recursive=group.settings.recursive
        )
        if not image_paths:
            return None

        # Convert to relative string paths
        images = [str(p.relative_to(self.image_service.image_dir)) for p in image_paths]

        import random
        next_img = random.choice(images)

        # Update endpoint history
        if endpoint.current_image:
            endpoint.image_history.append(endpoint.current_image)
        endpoint.image_history.extend(endpoint.forward_history)
        endpoint.forward_history.clear()
        endpoint.current_image = next_img

        # Increment cycle count (matches _advance_browsers behavior)
        group.current_cycle = (group.current_cycle + 1) % MAX_CYCLE_COUNT

        return next_img

    def rewind_endpoint_no_browser(self, endpoint: DisplayEndpoint) -> Optional[str]:
        """
        Rewind endpoint to previous image when no browser is connected.

        Used by the /display?action=previous URL and API endpoints.
        Returns the new current_image path, or None if no history.
        """
        if not endpoint.image_history:
            return endpoint.current_image  # Nothing to rewind to

        if endpoint.current_image:
            endpoint.forward_history.insert(0, endpoint.current_image)
        endpoint.current_image = endpoint.image_history.pop()

        return endpoint.current_image

    def _validate_image_path(self, path: str) -> bool:
        """Check if an image path still exists on disk."""
        if not path:
            return False
        full_path = self.image_service.image_dir / path
        return full_path.exists() and full_path.is_file()

    def _clean_endpoint_history(self, endpoint: DisplayEndpoint) -> int:
        """
        Remove invalid paths from endpoint history.

        Called during refresh_group_images and jump_to_image to clean up
        references to deleted images. Returns count of removed items.
        """
        removed = 0

        # Clean image_history
        valid_history = [p for p in endpoint.image_history if self._validate_image_path(p)]
        removed += len(endpoint.image_history) - len(valid_history)
        endpoint.image_history = valid_history

        # Clean forward_history
        valid_forward = [p for p in endpoint.forward_history if self._validate_image_path(p)]
        removed += len(endpoint.forward_history) - len(valid_forward)
        endpoint.forward_history = valid_forward

        # Validate current_image
        if endpoint.current_image and not self._validate_image_path(endpoint.current_image):
            logger.warning(
                f"Endpoint {endpoint.endpoint_id} current_image no longer exists: "
                f"{endpoint.current_image}"
            )
            endpoint.current_image = None
            removed += 1

        return removed
