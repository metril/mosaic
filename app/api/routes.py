"""
API Routes for Screensaver Application

Provides endpoints for random image serving and web interface.
"""

import os
import asyncio
from pathlib import Path
from typing import Optional, Literal
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.services.image_service import ImageService


router = APIRouter()
image_service = ImageService()
templates = Jinja2Templates(directory="templates")


@router.get("/")
async def root(
    request: Request,
    api: Optional[str] = Query(None, description="API mode - 'random-image' for image endpoint"),
    width: Optional[int] = Query(None, description="Target width in pixels", ge=1),
    height: Optional[int] = Query(None, description="Target height in pixels", ge=1),
    preserve_aspect: bool = Query(True, description="Preserve aspect ratio when resizing"),
    mode: Literal['fit', 'crop'] = Query('fit', description="Resize mode - 'fit' or 'crop'"),
    eink: bool = Query(False, description="Apply Spectra-6 E-Ink color mapping"),
    format: Optional[Literal['jpeg', 'png', 'webp', 'bmp', 'gif']] = Query(None, description="Output format (defaults to original format)")
):
    """
    Main endpoint - serves either the web interface or random image API.

    Query Parameters:
    - api: Set to 'random-image' for API mode
    - width: Target width for resizing (optional)
    - height: Target height for resizing (optional)
    - preserve_aspect: Whether to preserve aspect ratio (default: true)
    - mode: Resize mode - 'fit' (letterbox) or 'crop' (fill, default: fit)
    - eink: Apply Spectra-6 dithering for E-Ink displays (default: false)
    - format: Output format - 'jpeg', 'png', 'webp', 'bmp', 'gif' (defaults to original)

    Examples:
    - GET / - Web interface
    - GET /?api=random-image - Random image (original format)
    - GET /?api=random-image&format=png - Random image as PNG
    - GET /?api=random-image&width=800&height=600 - Resized image
    - GET /?api=random-image&eink=true&format=png - E-Ink dithered as PNG
    - GET /?api=random-image&width=800&height=600&eink=true&format=jpeg - Combined
    """
    # API mode - return random image
    if api == "random-image":
        # Run CPU-intensive image processing in thread pool to avoid blocking event loop
        result = await asyncio.get_event_loop().run_in_executor(
            None,  # Use default ThreadPoolExecutor
            image_service.process_image,
            width,
            height,
            preserve_aspect,
            mode,
            eink,
            format
        )

        if result is None:
            return JSONResponse(
                status_code=404,
                content={"error": "No images found in directory"}
            )

        buffer, content_type, filename = result

        # Get buffer size for Content-Length header
        buffer.seek(0, 2)  # Seek to end
        content_length = buffer.tell()
        buffer.seek(0)  # Seek back to beginning

        return StreamingResponse(
            buffer,
            media_type=content_type,
            headers={
                "Content-Length": str(content_length),
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Content-Disposition": f'inline; filename="{filename}"'
            }
        )

    # Default mode - serve web interface
    else:
        # Get list of all images for the JavaScript array
        image_files = image_service.get_image_files()

        # Get base URL from environment or use request host
        base_url = os.environ.get('BASE_URL', '')
        if not base_url:
            # Construct from request
            base_url = f"{request.url.scheme}://{request.url.netloc}"

        # Build image URLs
        image_urls = [f"{base_url}/images/{file.name}" for file in image_files]

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "image_urls": image_urls,
                "image_count": len(image_urls)
            }
        )


@router.get("/health")
async def health_check():
    """
    Health check endpoint.

    Returns:
        JSON with status and image count
    """
    image_files = image_service.get_image_files()

    return {
        "status": "healthy",
        "image_count": len(image_files),
        "image_directory": str(image_service.image_dir)
    }
