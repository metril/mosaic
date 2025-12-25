"""
Screensaver Application - FastAPI Main Entry Point

A modern image screensaver service with resizing and E-Ink color mapping capabilities.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware
import os
from pathlib import Path

from app.api.routes import router
from app.utils.logging import logger
from app.exceptions import PathSecurityError, NoImagesFoundError, ScreensaverException


# Image directory (used in lifespan and static mount)
images_path = Path(os.environ.get('IMAGE_DIR', '/www/images'))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan context manager.
    Handles startup and shutdown events (modern FastAPI pattern).
    """
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
    logger.info("=" * 60)

    yield  # Application runs here

    # Shutdown
    logger.info("Screensaver Application Shutting Down")


# Create FastAPI application with lifespan
app = FastAPI(
    title="Screensaver API",
    description="Modern screensaver service with image processing capabilities",
    version="2.0.0",
    lifespan=lifespan
)

# Middleware is applied in REVERSE order of addition
# Add GZip first so it's the outermost layer (compresses all responses)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Add CORS middleware (allow_credentials=False when allow_origins=["*"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory for images
if images_path.exists():
    app.mount("/images", StaticFiles(directory=str(images_path)), name="images")

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
