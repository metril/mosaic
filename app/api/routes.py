"""
API Routes for Screensaver Application

Provides endpoints for random image serving and web interface.
"""

import os
import asyncio
from pathlib import Path
from typing import Optional, Literal
from fastapi import APIRouter, Query, HTTPException, Path as PathParam
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.services.image_service import ImageService
from app.exceptions import PathSecurityError, NoImagesFoundError


router = APIRouter()
image_service = ImageService()
templates = Jinja2Templates(directory="templates")


@router.get("/{folder_path:path}")
async def root(
    request: Request,
    folder_path: str = PathParam("", description="Folder path (e.g., 'earth' or 'earth/folder1')"),
    api: Optional[str] = Query(None, description="API mode - 'random-image' for image endpoint"),
    width: Optional[int] = Query(None, description="Target width in pixels", ge=1),
    height: Optional[int] = Query(None, description="Target height in pixels", ge=1),
    preserve_aspect: bool = Query(True, description="Preserve aspect ratio when resizing"),
    mode: Literal['fit', 'crop'] = Query('fit', description="Resize mode - 'fit' or 'crop'"),
    eink: bool = Query(False, description="Apply Spectra-6 E-Ink color mapping"),
    format: Optional[Literal['jpeg', 'png', 'webp', 'bmp', 'gif']] = Query(None, description="Output format (defaults to original format)"),
    saturation: float = Query(2.5, description="E-Ink saturation multiplier (default: 2.5)", ge=0.0, le=10.0),
    contrast: float = Query(1.2, description="E-Ink contrast multiplier (default: 1.2)", ge=0.0, le=5.0),
    brightness: float = Query(1.1, description="E-Ink brightness multiplier (default: 1.1)", ge=0.0, le=5.0),
    sharpen: bool = Query(True, description="Apply sharpening for E-Ink preprocessing (default: true)"),
    recursive: bool = Query(False, description="Recursively search subfolders (default: false)"),
    folders: Optional[str] = Query(None, description="Comma-separated list of folder paths (alternative to path)"),
    also: Optional[str] = Query(None, description="Comma-separated list of additional folders (use with path)")
):
    """
    Main endpoint - serves either the web interface or random image API.

    Supports path-based folder navigation:
    - / - Root directory (top-level images only, unless recursive=true)
    - /earth - Images from earth folder (non-recursive)
    - /earth/folder1 - Images from earth/folder1 (non-recursive)

    Query Parameters:
    - api: Set to 'random-image' for API mode
    - width: Target width for resizing (optional)
    - height: Target height for resizing (optional)
    - preserve_aspect: Whether to preserve aspect ratio (default: true)
    - mode: Resize mode - 'fit' (letterbox) or 'crop' (fill, default: fit)
    - eink: Apply Spectra-6 dithering for E-Ink displays (default: false)
    - format: Output format - 'jpeg', 'png', 'webp', 'bmp', 'gif' (defaults to original)
    - saturation: E-Ink saturation multiplier 0.0-10.0 (default: 2.5)
    - contrast: E-Ink contrast multiplier 0.0-5.0 (default: 1.2)
    - brightness: E-Ink brightness multiplier 0.0-5.0 (default: 1.1)
    - sharpen: Apply sharpening for E-Ink (default: true)
    - recursive: Recursively search subfolders (default: false)
    - folders: Comma-separated folder paths (e.g., folders=earth,space)
    - also: Additional folders when using path (e.g., /earth?also=space,family)

    Examples:
    - GET / - Web interface (all images)
    - GET /?api=random-image - Random top-level image
    - GET /?api=random-image&recursive=true - Random from all folders
    - GET /earth?api=random-image - Random from earth folder
    - GET /earth?api=random-image&recursive=true - Random from earth and subfolders
    - GET /earth/folder1?api=random-image - Random from earth/folder1
    - GET /?folders=earth,space&api=random-image - Random from earth or space
    - GET /earth?also=space,family&api=random-image - Random from earth, space, or family
    - GET /earth?api=random-image&eink=true&format=png - E-Ink from earth folder
    """
    # Parse folder paths from URL path and query parameters
    folder_paths_list = []

    # Priority 1: Use 'folders' query param if provided
    if folders:
        folder_paths_list = [f.strip() for f in folders.split(',') if f.strip()]
    # Priority 2: Use URL path + 'also' query param
    elif folder_path or also:
        if folder_path:
            folder_paths_list.append(folder_path)
        if also:
            additional_folders = [f.strip() for f in also.split(',') if f.strip()]
            folder_paths_list.extend(additional_folders)

    # Convert empty list to None (means search base directory)
    final_folder_paths = folder_paths_list if folder_paths_list else None

    # API mode - return random image
    if api == "random-image":
        try:
            # Run CPU-intensive image processing in thread pool to avoid blocking event loop
            result = await asyncio.get_event_loop().run_in_executor(
                None,  # Use default ThreadPoolExecutor
                image_service.process_image,
                width,
                height,
                preserve_aspect,
                mode,
                eink,
                format,
                saturation,
                contrast,
                brightness,
                sharpen,
                final_folder_paths,
                recursive
            )

            if result is None:
                return JSONResponse(
                    status_code=404,
                    content={"error": "No images found"}
                )
        except PathSecurityError as e:
            # Security violation - return 400 Bad Request with generic message
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid folder path"}
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
        try:
            # Get list of images for the JavaScript array (with folder filtering if specified)
            image_files = image_service.get_image_files(final_folder_paths, recursive)

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
        except PathSecurityError as e:
            # Security violation - return 400 Bad Request
            raise HTTPException(status_code=400, detail="Invalid folder path")


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
