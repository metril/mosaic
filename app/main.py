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
from app.services.sync_manager import SyncManager
from app.utils.logging import logger
from app.exceptions import PathSecurityError, NoImagesFoundError, ScreensaverException

# Rate limiter for API endpoints
limiter = Limiter(key_func=get_remote_address)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Enable XSS protection in older browsers
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Content Security Policy (S6) - restrict script/style sources
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' wss: ws:; "
            "frame-ancestors 'none'"
        )
        return response


# Image directory (used in lifespan and static mount)
images_path = Path(os.environ.get('IMAGE_DIR', '/www/images'))


async def periodic_cleanup(sync_manager: SyncManager, interval: int = 30):
    """Periodically clean up stale browser connections with resilience (#27)."""
    consecutive_failures = 0
    max_failures = 5
    backoff_delay = interval

    while True:
        await asyncio.sleep(backoff_delay)
        try:
            await sync_manager.cleanup_stale_browsers(timeout_seconds=60)
            # Reset on success
            consecutive_failures = 0
            backoff_delay = interval
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Cleanup error (attempt {consecutive_failures}): {e}")

            if consecutive_failures >= max_failures:
                # Exponential backoff after repeated failures
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

        # Initialize SyncManager for synchronized multi-browser display
        app.state.sync_manager = SyncManager(image_service)
        logger.info("SyncManager initialized")

        # Start cleanup task for stale browser connections
        cleanup_task = asyncio.create_task(periodic_cleanup(app.state.sync_manager))
        logger.info("Cleanup task started")

        logger.info("=" * 60)

        yield  # Application runs here

    except Exception as e:
        logger.error(f"Lifespan error: {e}", exc_info=True)
        raise
    finally:
        # Shutdown
        if cleanup_task:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("Screensaver Application Shutting Down")


# Create FastAPI application with lifespan
app = FastAPI(
    title="Screensaver API",
    description="Modern screensaver service with image processing capabilities",
    version="2.0.0",
    lifespan=lifespan
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
    max_age=3600,  # 1 hour session
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
        content={"error": "Invalid path", "detail": str(exc)}
    )


@app.exception_handler(NoImagesFoundError)
async def no_images_exception_handler(request: Request, exc: NoImagesFoundError):
    """Handle no images found with 404 Not Found."""
    logger.info(f"No images found: {exc} - Path: {request.url.path}")
    return JSONResponse(
        status_code=404,
        content={"error": "No images found", "detail": str(exc)}
    )


@app.exception_handler(ScreensaverException)
async def screensaver_exception_handler(request: Request, exc: ScreensaverException):
    """Handle generic screensaver exceptions with 500 Internal Server Error."""
    logger.error(f"Application error: {exc} - Path: {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)}
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions with 500 Internal Server Error."""
    logger.error(f"Unexpected error: {exc} - Path: {request.url.path}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
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
