"""
Screensaver Application - FastAPI Main Entry Point

A modern image screensaver service with resizing and E-Ink color mapping capabilities.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os
from pathlib import Path

from app.api.routes import router


# Create FastAPI application
app = FastAPI(
    title="Screensaver API",
    description="Modern screensaver service with image processing capabilities",
    version="2.0.0"
)

# Add CORS middleware (optional, for web API access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory for images (configurable via IMAGE_DIR env var)
images_path = Path(os.environ.get('IMAGE_DIR', '/www/images'))
if images_path.exists():
    app.mount("/images", StaticFiles(directory=str(images_path)), name="images")

# Include API routes
app.include_router(router)


@app.on_event("startup")
async def startup_event():
    """
    Application startup event handler.
    """
    print("=" * 60)
    print("Screensaver Application Starting")
    print("=" * 60)
    print(f"Image Directory: {images_path}")
    print(f"Images Exist: {images_path.exists()}")

    if images_path.exists():
        image_count = len(list(images_path.glob("*")))
        print(f"Total Files: {image_count}")

    base_url = os.environ.get('BASE_URL', 'Not set (using dynamic detection)')
    print(f"Base URL: {base_url}")
    print("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """
    Application shutdown event handler.
    """
    print("Screensaver Application Shutting Down")


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
