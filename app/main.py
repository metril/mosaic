"""
Screensaver Application - FastAPI Main Entry Point

A modern image screensaver service with resizing and E-Ink color mapping capabilities.
"""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette_compress import CompressMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import os
from pathlib import Path

from app.api.routes import router, image_service
from app.auth.routes import router as auth_router
from app.auth.oidc import get_oidc_config
from app.auth.api_keys import APIKeyManager
from app.services.sync_manager import DisplayManager
from app.services.immich_provider import ImmichProvider
from app.services.homeassistant_provider import HomeAssistantProvider, HomeAssistantConfig
from app.services.file_watcher import ImageFileWatcher
from app.services.thumbnail_cache import ThumbnailCache, THUMBNAIL_CACHE_DIR
from app.utils.logging import logger
from app.exceptions import PathSecurityError, NoImagesFoundError, ScreensaverException, error_response
from pytimeparse import parse as parse_duration

# Rate limiter for API endpoints
limiter = Limiter(key_func=get_remote_address)

# Iframe embedding configuration (for Wallpanel, Home Assistant, etc.)
ALLOW_FRAME_EMBEDDING = os.environ.get("ALLOW_FRAME_EMBEDDING", "false").lower() == "true"
FRAME_ANCESTORS = os.environ.get("FRAME_ANCESTORS", "")  # e.g., "https://homeassistant.local:8123"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    # Routes that can be embedded in iframes (display-related)
    EMBEDDABLE_PATHS = ("/display", "/ws/display", "/api/image")

    def _is_embeddable_path(self, path: str) -> bool:
        """Check if path should allow iframe embedding."""
        return any(path.startswith(p) for p in self.EMBEDDABLE_PATHS)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Skip WebSocket upgrades (they don't need security headers)
        if request.headers.get("upgrade", "").lower() == "websocket":
            return response

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Determine frame policy based on path and config
        is_embeddable = ALLOW_FRAME_EMBEDDING and self._is_embeddable_path(request.url.path)

        if is_embeddable:
            # Allow iframe embedding for display routes
            if FRAME_ANCESTORS:
                frame_policy = f"frame-ancestors {FRAME_ANCESTORS}"
            else:
                frame_policy = "frame-ancestors *"
            # Don't set X-Frame-Options (let CSP frame-ancestors handle it)
        else:
            # Strict policy for admin and other routes
            response.headers["X-Frame-Options"] = "DENY"
            frame_policy = "frame-ancestors 'none'"

        # Content Security Policy (S6) - restrict script/style sources
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' wss: ws:; "
            f"{frame_policy}"
        )
        return response


# Image directory (used in lifespan and static mount)
images_path = Path(os.environ.get('IMAGE_DIR', '/www/images'))


async def periodic_cleanup(
    display_manager: DisplayManager,
    interval: int = int(os.environ.get("CLEANUP_INTERVAL", "30")),
    endpoint_cleanup_seconds: int = 86400,
    ha_provider_getter=None
):
    """Periodically clean up stale browser connections with resilience (#27)."""
    consecutive_failures = 0
    max_failures = 5
    backoff_delay = interval
    iteration_count = 0
    browser_stale_timeout = int(os.environ.get("BROWSER_STALE_TIMEOUT", "60"))
    # Run endpoint cleanup every hour (120 iterations at 30s interval)
    endpoint_cleanup_interval = 120

    # Health check every ~5 minutes (10 iterations at 30s interval)
    health_check_interval = 10

    while True:
        await asyncio.sleep(backoff_delay)
        iteration_count += 1
        had_failure = False

        try:
            # Stage 1: Clean up stale browsers
            try:
                await display_manager.cleanup_stale_browsers(timeout_seconds=browser_stale_timeout)
            except Exception as e:
                had_failure = True
                logger.error(f"Browser cleanup error: {e}")

            # Stage 2: Clean up anonymous endpoints (hourly)
            # Set ENDPOINT_CLEANUP=0 to disable cleanup entirely
            if endpoint_cleanup_seconds > 0 and iteration_count % endpoint_cleanup_interval == 0:
                try:
                    cleaned = await display_manager.cleanup_stale_endpoints(
                        timeout_seconds=endpoint_cleanup_seconds
                    )
                    if cleaned > 0:
                        logger.info(f"Cleaned up {cleaned} stale anonymous endpoints")
                except Exception as e:
                    had_failure = True
                    logger.error(f"Endpoint cleanup error: {e}")

            # Stage 3: Periodic health check for degraded groups
            if iteration_count % health_check_interval == 0:
                try:
                    recovered = await display_manager.health_check_groups()
                    if recovered > 0:
                        logger.info(f"Health check recovered {recovered} groups")
                except Exception as e:
                    had_failure = True
                    logger.error(f"Health check error: {e}")

            # Stage 4: Night-mode scheduling enforcement (F2) — every iteration
            try:
                ha_provider = ha_provider_getter() if ha_provider_getter else None
                await display_manager.enforce_schedules(ha_provider)
            except Exception as e:
                had_failure = True
                logger.error(f"Night-mode enforcement error: {e}")

            if had_failure:
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    backoff_delay = min(interval * (2 ** (consecutive_failures - max_failures)), 300)
                    logger.warning(f"Cleanup failing repeatedly, backing off to {backoff_delay}s")
            else:
                # Reset on full success
                consecutive_failures = 0
                backoff_delay = interval
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Cleanup error (attempt {consecutive_failures}): {e}")

            if consecutive_failures >= max_failures:
                backoff_delay = min(interval * (2 ** (consecutive_failures - max_failures)), 300)
                logger.warning(f"Cleanup failing repeatedly, backing off to {backoff_delay}s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan context manager.
    Handles startup and shutdown events (modern FastAPI pattern).
    """
    cleanup_task = None
    try:
        # Startup
        logger.info("=" * 60)
        logger.info("Screensaver Application Starting")
        logger.info("=" * 60)
        logger.info(f"Image Directory: {images_path}")
        logger.info(f"Images Exist: {images_path.exists()}")

        if images_path.exists():
            image_count = len(list(images_path.glob("*")))
            logger.info(f"Total Files: {image_count}")

        base_url = os.environ.get('BASE_URL', 'Not set (using dynamic detection)')
        logger.info(f"Base URL: {base_url}")

        # Initialize curation store (favorites / hidden images) — SQLite (F6)
        from app.services.curation_service import CurationService
        app.state.curation_service = CurationService(Path("config/curation.db"))
        logger.info("Curation service initialized")

        # Initialize DisplayManager for synchronized multi-browser display
        # The is_folder_locked callback checks if a folder is being updated by Immich pull
        # It uses a closure so it can access app.state.immich_provider after it's initialized
        def is_folder_locked(folder: str) -> bool:
            provider = getattr(app.state, 'immich_provider', None)
            return provider.is_folder_updating(folder) if provider else False

        def is_excluded(rel_path: str) -> bool:
            return app.state.curation_service.is_excluded(rel_path)

        app.state.display_manager = DisplayManager(
            image_service,
            is_folder_locked=is_folder_locked,
            is_excluded=is_excluded
        )
        logger.info("DisplayManager initialized")

        # Migrate old config file name if it exists
        old_config = Path("config/sync_groups.yaml")
        new_config = Path("config/display_groups.yaml")
        if old_config.exists() and not new_config.exists():
            old_config.rename(new_config)
            logger.info("Migrated config/sync_groups.yaml to config/display_groups.yaml")

        # Load display groups from config file
        loaded_groups = await app.state.display_manager.load_config()
        if loaded_groups > 0:
            logger.info(f"Loaded {loaded_groups} display groups from config")

        # Initialize Immich provider for pulling images from Immich servers
        # The on_pull_complete callback refreshes display groups after a pull finishes,
        # ensuring image pools and endpoint references are updated even if the file
        # watcher misses events (e.g. when shutil.rmtree removes the watched directory)
        async def on_pull_complete(folder: str) -> None:
            display_manager = getattr(app.state, 'display_manager', None)
            if display_manager:
                refreshed = await display_manager.refresh_groups_by_folder(folder)
                if refreshed:
                    logger.info(
                        f"Post-pull refresh for '{folder}', "
                        f"refreshed groups: {list(refreshed.keys())}"
                    )

        app.state.immich_provider = ImmichProvider(
            image_service.image_dir,
            on_pull_complete=on_pull_complete,
            curation=app.state.curation_service
        )
        loaded_pulls = await app.state.immich_provider.load_config()
        if loaded_pulls > 0:
            logger.info(f"Loaded {loaded_pulls} Immich pull configurations")

        # Start the cron scheduler for automatic Immich pulls (F1)
        from app.services.pull_scheduler import PullScheduler
        app.state.pull_scheduler = PullScheduler(app.state.immich_provider)
        app.state.pull_scheduler.start()
        logger.info("Immich pull scheduler started")

        # Initialize API key manager for image endpoint authentication
        app.state.api_key_manager = APIKeyManager()
        loaded_keys = await app.state.api_key_manager.load_config()
        if loaded_keys > 0:
            logger.info(f"Loaded {loaded_keys} API keys from config")

        # Initialize thumbnail cache for disk-based thumbnail serving
        app.state.thumbnail_cache = ThumbnailCache(
            cache_dir=THUMBNAIL_CACHE_DIR,
            image_dir=image_service.image_dir,
        )
        app.state.thumbnail_cache.start_pre_generation()
        logger.info("Thumbnail cache initialized, pre-generation started")

        # Set up file watcher for image directory changes
        # This handles both external file changes AND Immich pull completions
        async def on_folder_changed(folder: str) -> None:
            """Handle file system changes by refreshing affected display groups and thumbnail cache."""
            try:
                # Convert absolute path to relative for matching
                folder_path = Path(folder)
                relative = folder_path.relative_to(image_service.image_dir)
                rel_str = str(relative)

                # Invalidate and regenerate thumbnails for changed folder
                app.state.thumbnail_cache.invalidate_folder(rel_str)
                asyncio.create_task(app.state.thumbnail_cache.pre_generate_folder(rel_str))

                # Invalidate EXIF caption + blur caches (F4) — files may have changed
                app.state.display_manager.clear_caption_cache()

                # Drop cached processed-image bytes for the changed folder so memory
                # is reclaimed promptly (mtime-keying already guarantees freshness).
                from app.api.routes import processed_image_cache, invalidate_browse_cache
                processed_image_cache.clear_prefix(rel_str)

                # Drop the cached browse index so the new/changed files show up.
                invalidate_browse_cache()

                # Refresh affected display groups
                refreshed = await app.state.display_manager.refresh_groups_by_folder(rel_str)
                if refreshed:
                    logger.info(
                        f"Folder changed '{relative}', "
                        f"refreshed groups: {list(refreshed.keys())}"
                    )
            except ValueError:
                # Folder is outside image_dir, ignore
                pass

        app.state.file_watcher = ImageFileWatcher(image_service.image_dir, on_folder_changed)
        app.state.file_watcher.start(asyncio.get_running_loop())
        logger.info("File watcher started for image directory")

        # Initialize Home Assistant provider if configured
        ha_url = os.environ.get('HOMEASSISTANT_URL', '').strip()
        ha_token = os.environ.get('HOMEASSISTANT_TOKEN', '').strip()
        if ha_url and ha_token:
            ha_config = HomeAssistantConfig(
                url=ha_url,
                access_token=ha_token,
                enabled=True
            )
            app.state.ha_provider = HomeAssistantProvider(ha_config)
            logger.info(f"Home Assistant provider initialized: {ha_url}")
        else:
            app.state.ha_provider = None
            logger.debug("Home Assistant not configured (HOMEASSISTANT_URL/TOKEN not set)")

        # Start cleanup task for stale browser connections
        # ENDPOINT_CLEANUP: duration before anonymous endpoints are removed (default 24h, 0=disabled)
        # Supports: "30m", "24h", "7d", "1w", "2h30m", or seconds as plain number
        cleanup_duration_str = os.environ.get('ENDPOINT_CLEANUP', '24h')
        endpoint_cleanup_seconds = parse_duration(cleanup_duration_str) or 0
        cleanup_task = asyncio.create_task(
            periodic_cleanup(
                app.state.display_manager,
                endpoint_cleanup_seconds=endpoint_cleanup_seconds,
                ha_provider_getter=lambda: getattr(app.state, 'ha_provider', None)
            )
        )
        if endpoint_cleanup_seconds > 0:
            logger.info(f"Cleanup task started (endpoint cleanup: {cleanup_duration_str})")
        else:
            logger.info("Cleanup task started (endpoint cleanup: disabled)")

        logger.info("=" * 60)

        yield  # Application runs here

    except Exception as e:
        logger.error(f"Lifespan error: {e}", exc_info=True)
        raise
    finally:
        # Shutdown
        # Flush any pending debounced config save so a recent edit isn't lost.
        if hasattr(app.state, 'display_manager'):
            try:
                await app.state.display_manager.flush_save()
            except Exception as e:
                logger.warning(f"Config flush on shutdown failed: {e}")

        if hasattr(app.state, 'api_key_manager'):
            try:
                await app.state.api_key_manager.save_if_dirty()
            except Exception as e:
                logger.warning(f"API key flush on shutdown failed: {e}")

        if cleanup_task:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass

        # Stop file watcher
        if hasattr(app.state, 'file_watcher'):
            app.state.file_watcher.stop()

        # Shut down thumbnail cache process pool
        if hasattr(app.state, 'thumbnail_cache'):
            app.state.thumbnail_cache.shutdown()

        # Shut down the shared image-processing thread pool
        from app.api.routes import image_executor
        image_executor.shutdown(wait=False, cancel_futures=True)

        # Stop the Immich pull scheduler
        if hasattr(app.state, 'pull_scheduler'):
            app.state.pull_scheduler.shutdown()

        # Close Immich provider HTTP client
        if hasattr(app.state, 'immich_provider'):
            await app.state.immich_provider.close()

        # Close Home Assistant provider HTTP client
        if hasattr(app.state, 'ha_provider') and app.state.ha_provider:
            await app.state.ha_provider.close()

        logger.info("Screensaver Application Shutting Down")


# Create FastAPI application with lifespan
app = FastAPI(
    title="Screensaver API",
    description="Modern screensaver service with image processing capabilities",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,  # Disable default /docs (loads from CDN, blocked by CSP)
)

# Attach rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware is applied in REVERSE order of addition
# Add security headers first (outermost layer)
app.add_middleware(SecurityHeadersMiddleware)

# Add compression (only compresses text-based content types, not images)
app.add_middleware(CompressMiddleware, minimum_size=1000)

# Add session middleware for OIDC authentication
oidc_config = get_oidc_config()
app.add_middleware(
    SessionMiddleware,
    secret_key=oidc_config.session_secret,
    session_cookie="screensaver_session",
    max_age=int(os.environ.get("SESSION_MAX_AGE", "3600")),
    same_site="lax",
    https_only=os.environ.get("HTTPS_ONLY", "false").lower() == "true",
)

# Add CORS middleware (allow_credentials=True for session cookies)
allowed_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory for images
if images_path.exists():
    app.mount("/images", StaticFiles(directory=str(images_path)), name="images")

# Mount static CSS files for overlay themes
static_css_path = Path(__file__).parent / "static" / "css"
if static_css_path.exists():
    app.mount("/css", StaticFiles(directory=str(static_css_path)), name="css")

# Mount static JS files (date-fns for date formatting)
static_js_path = Path(__file__).parent / "static" / "js"
if static_js_path.exists():
    app.mount("/js", StaticFiles(directory=str(static_js_path)), name="js")

# Mount Swagger UI assets (self-hosted to comply with CSP)
static_swagger_path = Path(__file__).parent / "static" / "swagger"
if static_swagger_path.exists():
    app.mount("/swagger-static", StaticFiles(directory=str(static_swagger_path)), name="swagger")

# Mount built SPA assets (Vite output). Present only after the frontend has been
# built (Docker build copies web/dist here). Filenames are content-hashed, so the
# SPA index.html references /assets/<name>-<hash>.js. Mounting before the API
# router keeps these static files ahead of the "/{folder_path}" catch-all.
spa_assets_path = Path(__file__).parent / "static" / "spa" / "assets"
if spa_assets_path.exists():
    app.mount("/assets", StaticFiles(directory=str(spa_assets_path)), name="spa_assets")

# Custom /docs endpoint using self-hosted Swagger UI assets
from fastapi.openapi.docs import get_swagger_ui_html


@app.get("/docs", include_in_schema=False)
async def custom_swagger_docs():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_js_url="/swagger-static/swagger-ui-bundle.js",
        swagger_css_url="/swagger-static/swagger-ui.css",
    )


# Include auth routes
app.include_router(auth_router)

# Include API routes
app.include_router(router)


# Global exception handlers for uniform JSON error responses
@app.exception_handler(PathSecurityError)
async def path_security_exception_handler(request: Request, exc: PathSecurityError):
    """Handle path security violations with 400 Bad Request."""
    logger.warning(f"Path security violation: {exc} - Path: {request.url.path}")
    return JSONResponse(
        status_code=400,
        content=error_response("Invalid path", str(exc))
    )


@app.exception_handler(NoImagesFoundError)
async def no_images_exception_handler(request: Request, exc: NoImagesFoundError):
    """Handle no images found with 404 Not Found."""
    logger.info(f"No images found: {exc} - Path: {request.url.path}")
    return JSONResponse(
        status_code=404,
        content=error_response("No images found", str(exc))
    )


@app.exception_handler(ScreensaverException)
async def screensaver_exception_handler(request: Request, exc: ScreensaverException):
    """Handle generic screensaver exceptions with 500 Internal Server Error."""
    logger.error(f"Application error: {exc} - Path: {request.url.path}")
    return JSONResponse(
        status_code=500,
        content=error_response("Internal server error", str(exc))
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions with 500 Internal Server Error."""
    logger.error(f"Unexpected error: {exc} - Path: {request.url.path}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=error_response("Internal server error")
    )


if __name__ == "__main__":
    import uvicorn

    # Run the application with configurable log level
    log_level = os.environ.get('LOG_LEVEL', 'info').lower()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=80,
        log_level=log_level,
        access_log=True
    )
