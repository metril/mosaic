"""
Thumbnail Cache Service

Disk-based thumbnail cache with pre-generation support.
Caches thumbnails to avoid on-the-fly generation for every request.
Integrates with the file watcher for automatic invalidation when images change.
"""

import asyncio
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional

from app.utils.logging import logger


# Supported image formats (must match image_service.py)
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tiff', '.tif'}

# Format configuration from environment
THUMBNAIL_FORMAT = os.environ.get('THUMBNAIL_FORMAT', 'WEBP').upper()
THUMBNAIL_QUALITY = int(os.environ.get('THUMBNAIL_QUALITY', '80'))
THUMBNAIL_SIZE = int(os.environ.get('THUMBNAIL_SIZE', '200'))
THUMBNAIL_CACHE_DIR = Path(os.environ.get('THUMBNAIL_CACHE_DIR', '/www/thumbnails'))

# Map format name to file extension and MIME type
_FORMAT_INFO = {
    'WEBP': ('.webp', 'image/webp'),
    'JPEG': ('.jpg', 'image/jpeg'),
    'PNG': ('.png', 'image/png'),
}


def _generate_thumbnail_worker(
    source_path: str,
    cache_path: str,
    size: int,
    fmt: str,
    quality: int,
) -> str:
    """
    Worker function for ProcessPoolExecutor.

    Must be a top-level function (not a method) so it's picklable
    for multiprocessing. Returns cache_path on success.
    """
    from PIL import Image, ImageOps

    img = Image.open(source_path)
    img = ImageOps.fit(img, (size, size), Image.LANCZOS)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    save_kwargs = {'quality': quality, 'optimize': True}
    if fmt == 'WEBP':
        save_kwargs['method'] = 4  # Balance speed vs compression
    img.save(cache_path, fmt, **save_kwargs)
    return cache_path


class ThumbnailCache:
    """Disk-based thumbnail cache with pre-generation support."""

    def __init__(
        self,
        cache_dir: Path,
        image_dir: Path,
        size: int = THUMBNAIL_SIZE,
        fmt: str = THUMBNAIL_FORMAT,
        quality: int = THUMBNAIL_QUALITY,
        max_workers: int = int(os.environ.get("THUMBNAIL_MAX_WORKERS", "2")),
    ):
        self.cache_dir = cache_dir
        self.image_dir = image_dir
        self.size = size
        self.format = fmt if fmt in _FORMAT_INFO else 'WEBP'
        self.quality = max(1, min(100, quality))
        self.extension, self.media_type = _FORMAT_INFO[self.format]
        self._process_pool = ProcessPoolExecutor(max_workers=max_workers)
        self._semaphore = asyncio.Semaphore(max_workers * 4)
        self._pre_generation_task: Optional[asyncio.Task] = None

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"Thumbnail cache initialized: dir={self.cache_dir}, "
            f"format={self.format}, quality={self.quality}, size={self.size}"
        )

    def get_cache_path(self, relative_path: str) -> Path:
        """Map image relative path to cache file path."""
        return self.cache_dir / Path(relative_path).with_suffix(self.extension)

    def get(self, relative_path: str) -> Optional[Path]:
        """
        Return cached thumbnail path if valid, None otherwise.

        A cached thumbnail is valid if:
        - The cache file exists
        - The source file exists
        - The cache file is not older than the source file
        """
        cache_path = self.get_cache_path(relative_path)
        source_path = self.image_dir / relative_path

        if not cache_path.exists() or not source_path.exists():
            return None

        try:
            if cache_path.stat().st_mtime >= source_path.stat().st_mtime:
                return cache_path
        except OSError:
            pass

        return None

    def generate(self, relative_path: str) -> Optional[Path]:
        """
        Generate and cache a thumbnail synchronously.

        Used for individual cache misses via ThreadPoolExecutor.
        Returns cache path on success, None on failure.
        """
        source_path = self.image_dir / relative_path
        cache_path = self.get_cache_path(relative_path)

        if not source_path.exists() or source_path.is_symlink():
            return None

        try:
            _generate_thumbnail_worker(
                str(source_path), str(cache_path),
                self.size, self.format, self.quality
            )
            return cache_path
        except Exception as e:
            logger.warning(f"Failed to generate thumbnail for {relative_path}: {e}")
            # Clean up partial file
            cache_path.unlink(missing_ok=True)
            return None

    def invalidate(self, relative_path: str) -> None:
        """Delete cached thumbnail for a specific image."""
        cache_path = self.get_cache_path(relative_path)
        try:
            cache_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Failed to invalidate thumbnail cache for {relative_path}: {e}")

    def invalidate_folder(self, folder: str) -> None:
        """Delete all cached thumbnails for a folder."""
        cache_folder = self.cache_dir / folder
        if cache_folder.exists():
            try:
                shutil.rmtree(cache_folder)
                logger.debug(f"Invalidated thumbnail cache for folder: {folder}")
            except OSError as e:
                logger.warning(f"Failed to invalidate thumbnail cache folder {folder}: {e}")

    def _find_all_images(self) -> list[str]:
        """Find all image files and return their relative paths."""
        images = []
        for file_path in self.image_dir.rglob('*'):
            if (
                file_path.is_file()
                and not file_path.is_symlink()
                and file_path.suffix.lower() in SUPPORTED_FORMATS
            ):
                try:
                    rel_path = str(file_path.relative_to(self.image_dir))
                    images.append(rel_path)
                except ValueError:
                    pass
        return images

    def _find_folder_images(self, folder: str) -> list[str]:
        """Find all image files in a specific folder and return relative paths."""
        folder_path = self.image_dir / folder
        if not folder_path.exists():
            return []

        images = []
        for file_path in folder_path.rglob('*'):
            if (
                file_path.is_file()
                and not file_path.is_symlink()
                and file_path.suffix.lower() in SUPPORTED_FORMATS
            ):
                try:
                    rel_path = str(file_path.relative_to(self.image_dir))
                    images.append(rel_path)
                except ValueError:
                    pass
        return images

    async def _generate_one(self, relative_path: str, loop: asyncio.AbstractEventLoop) -> bool:
        """Generate a single thumbnail using the process pool with semaphore."""
        async with self._semaphore:
            source_path = str(self.image_dir / relative_path)
            cache_path = str(self.get_cache_path(relative_path))
            try:
                await loop.run_in_executor(
                    self._process_pool,
                    _generate_thumbnail_worker,
                    source_path, cache_path,
                    self.size, self.format, self.quality
                )
                return True
            except Exception as e:
                logger.warning(f"Failed to pre-generate thumbnail for {relative_path}: {e}")
                return False

    async def pre_generate_folder(self, folder: str) -> int:
        """
        Pre-generate thumbnails for all images in a folder.

        Called by file watcher after folder changes. Runs in background.
        Returns number of thumbnails generated.
        """
        images = self._find_folder_images(folder)
        if not images:
            return 0

        # Filter to only missing/stale thumbnails
        to_generate = [img for img in images if self.get(img) is None]
        if not to_generate:
            return 0

        loop = asyncio.get_running_loop()
        tasks = [self._generate_one(img, loop) for img in to_generate]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        generated = sum(1 for r in results if r is True)
        if generated > 0:
            logger.info(f"Pre-generated {generated}/{len(to_generate)} thumbnails for folder: {folder}")
        return generated

    async def pre_generate_all(self) -> None:
        """
        Startup task: generate all missing thumbnails and purge orphans.

        Runs as a background task — does not block server startup.
        """
        start_time = time.monotonic()
        logger.info("Starting thumbnail pre-generation...")

        # Find all images
        all_images = self._find_all_images()
        total = len(all_images)
        if total == 0:
            logger.info("No images found for thumbnail pre-generation")
            return

        # Filter to missing/stale only
        to_generate = [img for img in all_images if self.get(img) is None]
        logger.info(
            f"Found {total} images, {len(to_generate)} need thumbnail generation "
            f"({total - len(to_generate)} already cached)"
        )

        if to_generate:
            loop = asyncio.get_running_loop()
            generated = 0
            failed = 0

            # Process in batches for progress logging
            batch_size = 50
            for i in range(0, len(to_generate), batch_size):
                batch = to_generate[i:i + batch_size]
                tasks = [self._generate_one(img, loop) for img in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                batch_ok = sum(1 for r in results if r is True)
                batch_fail = len(batch) - batch_ok
                generated += batch_ok
                failed += batch_fail

                progress = i + len(batch)
                if progress < len(to_generate):
                    logger.info(f"Pre-generated {progress}/{len(to_generate)} thumbnails...")

            elapsed = time.monotonic() - start_time
            logger.info(
                f"Thumbnail pre-generation complete: {generated} generated, "
                f"{failed} failed, {total - len(to_generate)} cached, "
                f"{elapsed:.1f}s elapsed"
            )

        # Purge orphaned cache entries (thumbnails for deleted source images)
        purged = self._purge_orphans()
        if purged > 0:
            logger.info(f"Purged {purged} orphaned thumbnail cache entries")

    def _purge_orphans(self) -> int:
        """Remove cached thumbnails that no longer have a matching source image."""
        purged = 0
        if not self.cache_dir.exists():
            return 0

        for cache_file in self.cache_dir.rglob('*'):
            if not cache_file.is_file():
                continue

            # Reconstruct source path from cache path
            try:
                rel_cache = cache_file.relative_to(self.cache_dir)
            except ValueError:
                continue

            # Check all supported source extensions
            found_source = False
            stem_path = self.image_dir / rel_cache.with_suffix('')
            for ext in SUPPORTED_FORMATS:
                source_candidate = stem_path.with_suffix(ext)
                if source_candidate.exists() and not source_candidate.is_symlink():
                    found_source = True
                    break

            if not found_source:
                try:
                    cache_file.unlink()
                    purged += 1
                except OSError:
                    pass

        # Clean up empty directories in cache
        for dirpath in sorted(self.cache_dir.rglob('*'), reverse=True):
            if dirpath.is_dir():
                try:
                    dirpath.rmdir()  # Only removes if empty
                except OSError:
                    pass

        return purged

    def start_pre_generation(self) -> None:
        """Start background pre-generation task."""
        self._pre_generation_task = asyncio.create_task(self.pre_generate_all())

    def shutdown(self) -> None:
        """Shut down the process pool and cancel pre-generation."""
        if self._pre_generation_task and not self._pre_generation_task.done():
            self._pre_generation_task.cancel()
        self._process_pool.shutdown(wait=False)
        logger.info("Thumbnail cache shut down")
