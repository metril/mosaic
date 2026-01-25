"""
File System Watcher Service

Monitors image directories for changes and triggers sync group refreshes.
Uses watchdog library for cross-platform file system events.
"""
import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional, Set

from watchdog.observers import Observer
from watchdog.events import (
    FileSystemEventHandler,
    FileCreatedEvent,
    FileDeletedEvent,
    FileMovedEvent,
)

logger = logging.getLogger(__name__)


class ImageFolderHandler(FileSystemEventHandler):
    """Handler for image file changes."""

    SUPPORTED_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'
    }

    def __init__(
        self,
        callback: Callable[[str], Awaitable[None]],
        loop: asyncio.AbstractEventLoop
    ):
        self.callback = callback
        self.loop = loop
        self._pending_folders: Set[str] = set()
        self._debounce_task: Optional[asyncio.Task] = None

    def _is_image(self, path: str) -> bool:
        """Check if a file path is a supported image type."""
        return Path(path).suffix.lower() in self.SUPPORTED_EXTENSIONS

    def _schedule_callback(self, folder: str) -> None:
        """Debounce callbacks - wait for batch of changes to settle."""
        self._pending_folders.add(folder)

        # Cancel existing debounce task if any
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        # Schedule new debounce task
        self._debounce_task = self.loop.create_task(self._debounced_callback())

    async def _debounced_callback(self) -> None:
        """Wait for changes to settle, then fire callbacks."""
        await asyncio.sleep(2.0)  # Wait 2 seconds for changes to settle

        folders = self._pending_folders.copy()
        self._pending_folders.clear()

        for folder in folders:
            try:
                await self.callback(folder)
            except Exception as e:
                logger.error(f"Error in file watcher callback for {folder}: {e}")

    def on_created(self, event: FileCreatedEvent) -> None:
        """Handle file creation events."""
        if not event.is_directory and self._is_image(event.src_path):
            folder = str(Path(event.src_path).parent)
            self.loop.call_soon_threadsafe(
                lambda: self.loop.create_task(self._handle_change(folder))
            )

    def on_deleted(self, event: FileDeletedEvent) -> None:
        """Handle file deletion events."""
        if not event.is_directory and self._is_image(event.src_path):
            folder = str(Path(event.src_path).parent)
            self.loop.call_soon_threadsafe(
                lambda: self.loop.create_task(self._handle_change(folder))
            )

    def on_moved(self, event: FileMovedEvent) -> None:
        """Handle file move/rename events."""
        if not event.is_directory:
            # Check both source and destination paths
            if self._is_image(event.src_path) or self._is_image(event.dest_path):
                for path in [event.src_path, event.dest_path]:
                    folder = str(Path(path).parent)
                    self.loop.call_soon_threadsafe(
                        lambda f=folder: self.loop.create_task(self._handle_change(f))
                    )

    async def _handle_change(self, folder: str) -> None:
        """Handle a folder change by scheduling a debounced callback."""
        self._schedule_callback(folder)


class ImageFileWatcher:
    """Watches image directories for changes and triggers refresh callbacks."""

    def __init__(
        self,
        image_dir: Path,
        callback: Callable[[str], Awaitable[None]]
    ):
        """
        Initialize the file watcher.

        Args:
            image_dir: Base directory to watch for image changes
            callback: Async function to call when images change.
                      Receives the absolute path of the changed folder.
        """
        self.image_dir = image_dir
        self.callback = callback
        self.observer: Optional[Observer] = None
        self._handler: Optional[ImageFolderHandler] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Start watching the image directory.

        Args:
            loop: The asyncio event loop to use for callbacks
        """
        self._handler = ImageFolderHandler(self.callback, loop)
        self.observer = Observer()
        self.observer.schedule(
            self._handler,
            str(self.image_dir),
            recursive=True
        )
        self.observer.start()
        logger.info(f"File watcher started for {self.image_dir}")

    def stop(self) -> None:
        """Stop watching."""
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5.0)
            self.observer = None
            self._handler = None
            logger.info("File watcher stopped")
