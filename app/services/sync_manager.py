"""
Synchronization Manager for Multi-Browser Image Display

Manages synchronized image display across multiple browsers using WebSocket connections.
Each browser in a sync group displays a unique image, and all browsers switch images
at exactly the same time.
"""

import asyncio
import os
import random
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Any
from fastapi import WebSocket

from app.services.image_service import ImageService
from app.utils.logging import logger
from app.utils.metrics import metrics

# Constants
PRELOAD_LEAD_TIME_SECONDS = 5  # Time before switch to send preload command (P9)
MAX_CYCLE_COUNT = 2**31  # Wrap cycle counter to prevent overflow (S14)
MAX_IMAGE_HISTORY = 100  # Maximum images to keep in browser history for "previous"

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


@dataclass
class SyncGroupSettings:
    """All configurable parameters for a sync group"""
    duration: int = 30  # Cycle duration in seconds (5-3600)
    folders: Optional[str] = None  # Comma-separated folder paths
    recursive: bool = False  # Search subfolders
    width: Optional[int] = None  # Target width
    height: Optional[int] = None  # Target height
    preserve_aspect: bool = True  # Preserve aspect ratio
    mode: str = "fit"  # "fit" or "crop"
    pad_color: str = "black"  # Letterbox padding color
    eink: bool = False  # E-Ink color mapping
    format: Optional[str] = None  # Output format
    saturation: float = 2.5  # E-Ink saturation
    contrast: float = 1.2  # E-Ink contrast
    brightness: float = 1.1  # E-Ink brightness
    sharpen: bool = True  # E-Ink sharpening

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncGroupSettings":
        """Create from dictionary"""
        # Filter out any unknown keys
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered_data)


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime (P3)."""
    return datetime.now(timezone.utc)


@dataclass
class BrowserState:
    """State of a connected browser in a sync group"""
    browser_id: str
    websocket: WebSocket
    current_image: Optional[str] = None
    next_image: Optional[str] = None
    image_history: List[str] = field(default_factory=list)  # History for "previous" control
    status: BrowserStatus = BrowserStatus.CONNECTING
    connected_at: datetime = field(default_factory=_utc_now)  # Timezone-aware (P3)
    last_heartbeat: datetime = field(default_factory=_utc_now)  # Timezone-aware (P3)


class ImagePool:
    """
    Manages image assignment to ensure unique images per browser
    and no repeats until all images have been shown.
    """

    def __init__(self, image_service: ImageService, settings: SyncGroupSettings):
        self.image_service = image_service
        self.settings = settings
        self._all_images: List[Path] = []
        self._all_paths_cache: Optional[List[str]] = None  # Cached string paths (#26)
        self._shown_images: Set[str] = set()  # Images shown this complete cycle
        self._assigned_images: Dict[str, str] = {}  # browser_id -> image path
        self._lock = asyncio.Lock()  # Lock for atomic assignment (#1)
        self._refresh_images()

    def _refresh_images(self) -> None:
        """Reload the list of available images based on settings"""
        folder_paths = None
        if self.settings.folders:
            folder_paths = [f.strip() for f in self.settings.folders.split(",") if f.strip()]

        self._all_images = self.image_service.get_image_files(
            folder_paths=folder_paths,
            recursive=self.settings.recursive
        )
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

        IMPORTANT: Caller must hold the ImagePool._lock or SyncGroup._lock
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

    def update_settings(self, settings: SyncGroupSettings) -> None:
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


@dataclass
class SyncGroup:
    """A synchronized group of browsers displaying images"""
    group_id: str
    settings: SyncGroupSettings
    image_pool: ImagePool
    browsers: Dict[str, BrowserState] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    current_cycle: int = 0
    next_switch_time: Optional[datetime] = None
    paused: bool = False  # Pause/play state for admin controls
    _cycle_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "group_id": self.group_id,
            "browser_count": len(self.browsers),
            "current_cycle": self.current_cycle,
            "paused": self.paused,
            "created_at": self.created_at.isoformat() + "Z",
            "next_switch_at": self.next_switch_time.isoformat() + "Z" if self.next_switch_time else None,
            "settings": self.settings.to_dict(),
            "browsers": [
                {
                    "browser_id": b.browser_id,
                    "status": b.status.value,
                    "current_image": b.current_image,
                    "connected_at": b.connected_at.isoformat() + "Z"
                }
                for b in self.browsers.values()
            ]
        }


class SyncManager:
    """
    Manages all sync groups and coordinates browser synchronization.
    """

    def __init__(self, image_service: ImageService):
        self.image_service = image_service
        self.groups: Dict[str, SyncGroup] = {}
        self._lock = asyncio.Lock()
        self._base_url: str = ""

    def set_base_url(self, base_url: str) -> None:
        """Set the base URL for image URLs"""
        self._base_url = base_url.rstrip("/")

    async def create_group(
        self,
        group_id: str,
        settings: Optional[SyncGroupSettings] = None
    ) -> SyncGroup:
        """Create a new sync group"""
        async with self._lock:
            if group_id in self.groups:
                raise ValueError(f"Group '{group_id}' already exists")

            # Check group limit (A10)
            if len(self.groups) >= MAX_GROUPS:
                raise ValueError(f"Maximum number of groups ({MAX_GROUPS}) reached")

            if settings is None:
                settings = SyncGroupSettings()

            image_pool = ImagePool(self.image_service, settings)
            group = SyncGroup(
                group_id=group_id,
                settings=settings,
                image_pool=image_pool
            )
            self.groups[group_id] = group
            metrics.record_group_created()  # A6
            logger.info(f"Created sync group: {group_id}")
            return group

    async def get_group(self, group_id: str) -> Optional[SyncGroup]:
        """Get a sync group by ID"""
        return self.groups.get(group_id)

    async def delete_group(self, group_id: str) -> bool:
        """Delete a sync group and disconnect all browsers"""
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
            logger.info(f"Deleted sync group: {group_id}")
            return True

    async def update_group_settings(
        self,
        group_id: str,
        updates: Dict[str, Any]
    ) -> Optional[SyncGroup]:
        """Update settings for a sync group and notify all browsers"""
        group = self.groups.get(group_id)
        if not group:
            return None

        async with group._lock:
            # Update settings
            current_dict = group.settings.to_dict()
            current_dict.update(updates)
            group.settings = SyncGroupSettings.from_dict(current_dict)
            group.image_pool.update_settings(group.settings)

            # Notify all browsers of settings change
            message = {
                "type": "SETTINGS_CHANGED",
                "settings": group.settings.to_dict()
            }
            await self._broadcast_to_group(group, message)

            # Log only field names, not values (S11)
            logger.info(f"Updated settings for group {group_id}: {list(updates.keys())}")
            return group

    async def list_groups(self) -> List[Dict[str, Any]]:
        """List all active sync groups"""
        return [group.to_dict() for group in self.groups.values()]

    async def add_browser(
        self,
        group_id: str,
        websocket: WebSocket
    ) -> Optional[BrowserState]:
        """
        Add a browser to a sync group.

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

            browser_id = uuid.uuid4().hex[:16]  # 64-bit uniqueness (P4)
            browser = BrowserState(
                browser_id=browser_id,
                websocket=websocket
            )

            async with group._lock:
                group.browsers[browser_id] = browser

                # Assign initial image (lock held for atomicity #1)
                image_path = group.image_pool.assign_unique_image(browser_id)
                if image_path:
                    browser.current_image = image_path

                # Start cycle task if needed (#2, #11) and not paused
                # Check both: first browser OR crashed task
                should_start_task = (
                    not group.paused and
                    (len(group.browsers) == 1 or
                     (group._cycle_task is not None and group._cycle_task.done()))
                )
                if should_start_task:
                    if group._cycle_task and group._cycle_task.done():
                        logger.warning(f"Restarting crashed cycle task for {group_id}")
                    group._cycle_task = asyncio.create_task(
                        self._run_cycle_loop(group)
                    )

            metrics.record_connection()  # A6
            logger.info(f"Browser {browser_id} joined group {group_id}")
            return browser

    async def remove_browser(self, group_id: str, browser_id: str) -> None:
        """Remove a browser from a sync group"""
        group = self.groups.get(group_id)
        if not group:
            return

        async with group._lock:
            if browser_id in group.browsers:
                group.image_pool.release_image(browser_id)
                del group.browsers[browser_id]
                metrics.record_disconnection()  # A6
                logger.info(f"Browser {browser_id} left group {group_id}")

            # Stop cycle task if no browsers left
            if not group.browsers and group._cycle_task:
                group._cycle_task.cancel()
                try:
                    await group._cycle_task
                except asyncio.CancelledError:
                    pass
                group._cycle_task = None

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
                # Move next_image to current_image, push old to history
                if browser.next_image:
                    # Push current to history before replacing (for "previous" control)
                    if browser.current_image:
                        browser.image_history.append(browser.current_image)
                        # Cap history at MAX_IMAGE_HISTORY
                        if len(browser.image_history) > MAX_IMAGE_HISTORY:
                            browser.image_history.pop(0)
                    browser.current_image = browser.next_image
                    browser.next_image = None
                logger.debug(f"Browser {browser_id} switch complete")

            elif msg_type == "HEARTBEAT":
                browser.last_heartbeat = _utc_now()

    def build_image_url(self, image_path: str, settings: SyncGroupSettings) -> str:
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
        group: SyncGroup,
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

    async def _run_cycle_loop(self, group: SyncGroup) -> None:
        """Main loop that coordinates image cycling for a group"""
        logger.info(f"Starting cycle loop for group {group.group_id}")

        try:
            while True:
                try:
                    duration = group.settings.duration

                    # Calculate next switch time
                    group.next_switch_time = _utc_now()

                    # Wait until PRELOAD_LEAD_TIME_SECONDS before switch (P9)
                    preload_delay = max(0, duration - PRELOAD_LEAD_TIME_SECONDS)
                    await asyncio.sleep(preload_delay)

                    # Prepare next images for all browsers
                    switch_at = _utc_now().timestamp() * 1000 + (PRELOAD_LEAD_TIME_SECONDS * 1000)
                    async with group._lock:
                        # Take snapshot for iteration and cache settings (#25)
                        browsers_to_update = list(group.browsers.values())
                        settings = group.settings

                    for browser in browsers_to_update:
                        async with group._lock:
                            # Assign under lock for atomicity (#1)
                            next_image = group.image_pool.assign_unique_image(browser.browser_id)
                            if next_image:
                                browser.next_image = next_image
                                browser.status = BrowserStatus.PRELOADING

                        if next_image:
                            # Build URL once per image (#25 - partially, each browser gets unique image)
                            image_url = self.build_image_url(next_image, settings)
                            try:
                                await asyncio.wait_for(
                                    browser.websocket.send_json({
                                        "type": "PRELOAD",
                                        "image_url": image_url,
                                        "image_id": next_image,
                                        "switch_at": switch_at
                                    }),
                                    timeout=5.0
                                )
                            except asyncio.TimeoutError:
                                logger.warning(f"Preload send timeout for {browser.browser_id}")
                            except Exception as e:
                                logger.warning(f"Failed to send preload: {e}")

                    # Wait remaining time until switch
                    remaining = PRELOAD_LEAD_TIME_SECONDS if preload_delay > 0 else duration
                    await asyncio.sleep(remaining)

                    # Send synchronized switch command (S14 - wrap to prevent overflow)
                    group.current_cycle = (group.current_cycle + 1) % MAX_CYCLE_COUNT
                    metrics.record_cycle()  # A6
                    switch_timestamp = _utc_now().timestamp() * 1000

                    await self._broadcast_to_group(group, {
                        "type": "SWITCH",
                        "timestamp": switch_timestamp,
                        "cycle": group.current_cycle
                    })

                    logger.debug(f"Group {group.group_id} cycle {group.current_cycle} switch")

                except asyncio.CancelledError:
                    raise  # Re-raise cancellation
                except Exception as e:
                    # Catch all other exceptions and continue (#3)
                    metrics.record_error()  # A6
                    logger.error(f"Error in cycle loop for {group.group_id}: {e}", exc_info=True)
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
            # Restart the cycle task if there are browsers
            if len(group.browsers) > 0:
                group._cycle_task = asyncio.create_task(
                    self._run_cycle_loop(group)
                )
            logger.info(f"Group {group_id} resumed")

        return True

    async def skip_group(self, group_id: str) -> bool:
        """Skip to next image immediately."""
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

        # Perform immediate advance (outside lock to allow message sending)
        await self._advance_browsers(group)

        async with group._lock:
            # Restart cycle task if not paused
            if not group.paused and group.browsers:
                group._cycle_task = asyncio.create_task(
                    self._run_cycle_loop(group)
                )

        logger.info(f"Group {group_id} skipped to next")
        return True

    async def previous_group(self, group_id: str) -> bool:
        """Go to previous image - each browser goes to its own previous image."""
        group = self.groups.get(group_id)
        if not group or not group.browsers:
            return False

        # Check if any browser has history
        has_history = any(
            len(browser.image_history) > 0
            for browser in group.browsers.values()
        )
        if not has_history:
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
            # Restart cycle task if not paused
            if not group.paused and group.browsers:
                group._cycle_task = asyncio.create_task(
                    self._run_cycle_loop(group)
                )

        logger.info(f"Group {group_id} went to previous")
        return True

    async def _advance_browsers(self, group: SyncGroup) -> None:
        """Immediately advance all browsers to next image."""
        settings = group.settings
        switch_at = _utc_now().timestamp() * 1000 + 500  # 500ms delay for preload

        for browser in list(group.browsers.values()):
            # Push current to history
            if browser.current_image:
                browser.image_history.append(browser.current_image)
                if len(browser.image_history) > MAX_IMAGE_HISTORY:
                    browser.image_history.pop(0)

            # Assign new image
            async with group._lock:
                next_image = group.image_pool.assign_unique_image(browser.browser_id)
            if not next_image:
                continue

            browser.next_image = next_image
            browser.status = BrowserStatus.PRELOADING

            # Send preload
            image_url = self.build_image_url(next_image, settings)
            try:
                await asyncio.wait_for(
                    browser.websocket.send_json({
                        "type": "PRELOAD",
                        "image_url": image_url,
                        "image_id": next_image,
                        "switch_at": switch_at
                    }),
                    timeout=5.0
                )
            except Exception as e:
                logger.warning(f"Failed to send preload to {browser.browser_id}: {e}")

        # Brief delay then send switch
        await asyncio.sleep(0.5)

        group.current_cycle = (group.current_cycle + 1) % MAX_CYCLE_COUNT
        metrics.record_cycle()
        switch_timestamp = _utc_now().timestamp() * 1000

        await self._broadcast_to_group(group, {
            "type": "SWITCH",
            "timestamp": switch_timestamp,
            "cycle": group.current_cycle
        })

    async def _rewind_browsers(self, group: SyncGroup) -> None:
        """Rewind each browser to its own previous image."""
        settings = group.settings

        for browser in list(group.browsers.values()):
            if not browser.image_history:
                continue  # No history for this browser

            # Pop from history
            previous_image = browser.image_history.pop()

            # Release current image back to pool if assigned
            async with group._lock:
                if browser.current_image:
                    group.image_pool.release_image(browser.browser_id)

            browser.current_image = previous_image
            browser.next_image = None
            browser.status = BrowserStatus.READY

            # Send ASSIGN to show the previous image immediately
            image_url = self.build_image_url(previous_image, settings)
            try:
                await asyncio.wait_for(
                    browser.websocket.send_json({
                        "type": "ASSIGN",
                        "image_url": image_url,
                        "image_id": previous_image,
                        "cycle": group.current_cycle
                    }),
                    timeout=5.0
                )
            except Exception as e:
                logger.warning(f"Failed to send assign to {browser.browser_id}: {e}")
