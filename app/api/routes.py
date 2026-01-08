"""
API Routes for Screensaver Application

Provides endpoints for random image serving and web interface.
"""

import os
import asyncio
import json
import hashlib
from email.utils import formatdate
from io import BytesIO
from pathlib import Path
from typing import Optional, Literal, Dict, Any
from urllib.parse import unquote
from fastapi import APIRouter, Query, HTTPException, Path as PathParam, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, Response, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.services.image_service import ImageService
from app.services.sync_manager import SyncManager, SyncGroupSettings
from app.auth.dependencies import get_current_user, get_optional_user, User, get_csrf_token, verify_csrf_token
from app.auth.oidc import get_oidc_config
from app.exceptions import PathSecurityError, NoImagesFoundError, error_response
from app.utils.path_security import resolve_image_path
from app.utils.logging import logger
from app.utils.metrics import metrics

# Allowed WebSocket origins (comma-separated in env, empty = allow all)
ALLOWED_WS_ORIGINS = os.environ.get("ALLOWED_WS_ORIGINS", "").split(",")
ALLOWED_WS_ORIGINS = [o.strip() for o in ALLOWED_WS_ORIGINS if o.strip()]


# Configuration from environment variables
THUMBNAIL_SIZE = int(os.environ.get('THUMBNAIL_SIZE', '200'))
MAX_IMAGE_DIMENSION = int(os.environ.get('MAX_IMAGE_DIMENSION', '8192'))
EINK_SATURATION = float(os.environ.get('EINK_SATURATION', '2.5'))
EINK_CONTRAST = float(os.environ.get('EINK_CONTRAST', '1.2'))
EINK_BRIGHTNESS = float(os.environ.get('EINK_BRIGHTNESS', '1.1'))
EINK_SHARPEN = os.environ.get('EINK_SHARPEN', 'true').lower() == 'true'


router = APIRouter()
image_service = ImageService()
templates = Jinja2Templates(directory="templates")


def iter_buffer_with_cleanup(buffer: BytesIO, chunk_size: int = 65536):
    """
    Generator that yields buffer content in chunks and closes it when done.

    Ensures proper resource cleanup for BytesIO buffers passed to StreamingResponse.

    Args:
        buffer: BytesIO buffer to read from
        chunk_size: Size of chunks to yield (default 64KB)

    Yields:
        Bytes chunks from the buffer
    """
    try:
        while True:
            chunk = buffer.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        buffer.close()

@router.get("/favicon.ico")
async def favicon():
    """Serve favicon.ico file."""
    return FileResponse(
        "app/static/favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"}
    )


@router.get("/health", responses={
    200: {
        "description": "Health status with image count",
        "content": {
            "application/json": {
                "example": {"status": "healthy", "image_count": 42, "image_directory": "/www/images"}
            }
        }
    }
})
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


@router.get("/metrics", responses={
    200: {
        "description": "Application metrics for observability (A6)",
        "content": {
            "application/json": {
                "example": {
                    "uptime_seconds": 3600.5,
                    "counters": {
                        "websocket_connections_total": 100,
                        "groups_created_total": 5
                    },
                    "gauges": {
                        "active_groups": 2,
                        "active_browsers": 10
                    }
                }
            }
        }
    }
})
async def get_metrics():
    """
    Return application metrics for observability.

    Provides counters (monotonically increasing) and gauges (current values)
    for monitoring sync group and WebSocket connection health.
    """
    return metrics.to_dict()


@router.get("/browse", responses={
    200: {
        "description": "Directory tree with folder statistics",
        "content": {
            "application/json": {
                "example": {
                    "name": "images",
                    "type": "folder",
                    "total_folders": 5,
                    "total_files": 150,
                    "children": []
                }
            }
        }
    }
})
async def browse_images(
    request: Request,
    format: Optional[Literal['json', 'html']] = Query(None, description="Output format - 'json' or 'html' (default)")
):
    """
    Browse all folders, subfolders, and image files in a tree structure.

    Returns complete recursive directory tree in a single response with folder statistics.
    Client-side JavaScript handles expand/collapse for folders.

    Query Parameters:
    - format: 'json' for API access, 'html' (default) for interactive web interface

    Examples:
    - GET /browse - Interactive HTML file explorer with statistics
    - GET /browse?format=json - JSON directory tree API with statistics

    Returns:
        HTML: Interactive file explorer with collapsible folder tree and statistics
        JSON: Complete directory tree structure with metadata and statistics
    """
    # Build complete directory tree with statistics (always enabled)
    tree_data = await asyncio.get_event_loop().run_in_executor(
        None,
        image_service.build_directory_tree
    )

    # Return JSON if requested
    if format == 'json':
        return JSONResponse(content=tree_data)

    # Return HTML by default
    return templates.TemplateResponse(
        "browse.html",
        {
            "request": request,
            "total_folders": tree_data["total_folders"],
            "total_files": tree_data["total_files"],
            "tree_json": json.dumps(tree_data)
        }
    )


@router.get("/thumbnail/{image_path:path}", responses={
    200: {"description": "Thumbnail image (JPEG)", "content": {"image/jpeg": {}}},
    400: {"description": "Invalid image path", "content": {"application/json": {"example": {"error": "Invalid image path"}}}},
    404: {"description": "Image not found", "content": {"application/json": {"example": {"error": "Image not found"}}}}
})
async def get_thumbnail(image_path: str):
    """
    Generate and serve a thumbnail for a specific image.

    Thumbnails are generated on-the-fly at 200x200px with crop mode.
    This endpoint is used by the browse interface for lazy-loaded thumbnails.

    Args:
        image_path: Relative path to the image file (e.g., "earth/photo.jpg")

    Returns:
        Thumbnail image (200x200px, cropped to square)

    Examples:
    - GET /thumbnail/earth/photo.jpg
    - GET /thumbnail/space/nebula.png
    - GET /thumbnail/landscape/%D0%9C%D0%BE%D0%B6%D0%B6%D0%B5%D0%B2%D0%B5%D0%BB%D0%BE%D0%B2%D0%B0%D1%8F.jpg (Cyrillic)
    """
    try:
        # Decode URL-encoded characters (handles Cyrillic, Chinese, emoji, etc.)
        image_path = unquote(image_path)

        # Validate path using centralized security function
        full_path = resolve_image_path(image_path, image_service.image_dir)

        # Security check: ensure file exists and is not a symlink
        if not full_path.exists() or full_path.is_symlink():
            return JSONResponse(
                status_code=404,
                content=error_response("Image not found")
            )

        # Security check: ensure it's actually within image_dir
        try:
            full_path.relative_to(image_service.image_dir)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content=error_response("Invalid image path")
            )

        # Load and resize image in thread pool
        def process_thumbnail():
            image = image_service.load_image(full_path)
            # Generate thumbnail with configurable size (crop mode for square)
            thumbnail = image_service.resize_image(
                image,
                width=THUMBNAIL_SIZE,
                height=THUMBNAIL_SIZE,
                preserve_aspect=True,
                mode='crop'
            )

            # Convert to JPEG for thumbnails (smaller file size)
            if thumbnail.mode != 'RGB':
                thumbnail = thumbnail.convert('RGB')

            buffer = BytesIO()
            thumbnail.save(buffer, format='JPEG', quality=85, optimize=True)
            buffer.seek(0)
            return buffer

        buffer = await asyncio.get_event_loop().run_in_executor(None, process_thumbnail)

        # Get buffer size (read all content, then reset)
        content = buffer.getvalue()
        content_length = len(content)
        buffer.seek(0)

        return StreamingResponse(
            iter_buffer_with_cleanup(buffer),
            media_type="image/jpeg",
            headers={
                "Content-Length": str(content_length),
                "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
                "Content-Disposition": f'inline; filename="thumb_{Path(image_path).name}"'
            }
        )

    except PathSecurityError:
        return JSONResponse(
            status_code=400,
            content=error_response("Invalid image path")
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=error_response("Failed to generate thumbnail")
        )


@router.get("/metadata/{image_path:path}", responses={
    200: {
        "description": "Image metadata with optional EXIF data",
        "content": {
            "application/json": {
                "example": {
                    "name": "photo.jpg",
                    "path": "landscape/photo.jpg",
                    "size": 1024000,
                    "width": 1920,
                    "height": 1080,
                    "format": "JPEG",
                    "modified": "2024-01-15T10:30:00Z"
                }
            }
        }
    },
    400: {"description": "Invalid image path"},
    404: {"description": "Image not found"}
})
async def get_image_metadata(
    image_path: str,
    include_exif: bool = Query(False, description="Include EXIF data (default: false)")
):
    """
    Get detailed metadata for a specific image, optionally including EXIF data.

    Examples:
    - GET /metadata/landscape/photo.jpg
    - GET /metadata/landscape/photo.jpg?include_exif=true

    Returns JSON with:
    - Basic metadata: name, path, size, dimensions, format, modified date
    - EXIF data (if include_exif=true): camera info, settings, GPS, timestamps
    """
    try:
        # Decode URL-encoded characters (handles Cyrillic, Chinese, emoji, etc.)
        image_path = unquote(image_path)

        # Validate path using centralized security function
        full_path = resolve_image_path(image_path, image_service.image_dir)

        # Check if file exists
        if not full_path.exists() or not full_path.is_file():
            return JSONResponse(
                status_code=404,
                content=error_response("Image not found")
            )

        # Get metadata (runs in thread pool for EXIF extraction)
        def extract_metadata():
            return image_service.get_image_metadata(full_path, include_exif=include_exif)

        metadata = await asyncio.get_event_loop().run_in_executor(None, extract_metadata)

        return JSONResponse(content=metadata)

    except PathSecurityError:
        return JSONResponse(
            status_code=400,
            content=error_response("Invalid image path")
        )
    except Exception as e:
        logger.error(f"Failed to extract metadata for {image_path}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=error_response("Failed to extract metadata")
        )


@router.api_route("/api/image/{image_path:path}", methods=["GET", "HEAD"], responses={
    200: {"description": "Processed image", "content": {"image/*": {}}},
    304: {"description": "Not Modified (conditional request)"},
    400: {"description": "Invalid image path"},
    404: {"description": "Image not found"}
})
async def serve_processed_image(
    request: Request,
    image_path: str,
    width: Optional[int] = Query(None, description=f"Target width in pixels (max {MAX_IMAGE_DIMENSION})", ge=1, le=MAX_IMAGE_DIMENSION),
    height: Optional[int] = Query(None, description=f"Target height in pixels (max {MAX_IMAGE_DIMENSION})", ge=1, le=MAX_IMAGE_DIMENSION),
    preserve_aspect: bool = Query(True, description="Preserve aspect ratio"),
    mode: Literal['fit', 'crop'] = Query('fit', description="Resize mode"),
    pad_color: str = Query("black", description="Letterbox padding color (CSS: name, hex, rgb())"),
    eink: bool = Query(False, description="Apply E-Ink color mapping"),
    format: Optional[Literal['jpeg', 'png', 'webp', 'bmp', 'gif']] = Query(None),
    saturation: Optional[float] = Query(None, description=f"E-Ink saturation (default: {EINK_SATURATION})", ge=0.0, le=10.0),
    contrast: Optional[float] = Query(None, description=f"E-Ink contrast (default: {EINK_CONTRAST})", ge=0.0, le=5.0),
    brightness: Optional[float] = Query(None, description=f"E-Ink brightness (default: {EINK_BRIGHTNESS})", ge=0.0, le=5.0),
    sharpen: Optional[bool] = Query(None, description=f"Apply sharpening (default: {EINK_SHARPEN})"),
):
    """
    Serve a specific image with optional processing (resize, E-Ink, format conversion).

    Unlike /images/ static mount, this processes query parameters for dynamic image manipulation.

    Examples:
    - /api/image/landscape/photo.jpg - Original image
    - /api/image/landscape/photo.jpg?eink=true - E-Ink processed with defaults
    - /api/image/landscape/photo.jpg?eink=true&saturation=3.0&contrast=1.5
    - /api/image/landscape/photo.jpg?width=800&height=600&mode=crop
    """
    try:
        # Decode URL-encoded path
        image_path = unquote(image_path)

        # Validate path using centralized security function
        full_path = resolve_image_path(image_path, image_service.image_dir)

        # Check file exists and is not a symlink
        if not full_path.exists() or not full_path.is_file() or full_path.is_symlink():
            return JSONResponse(
                status_code=404,
                content=error_response("Image not found")
            )

        # Process image in thread pool (CPU-intensive operations)
        def process():
            from app.utils.color_mapping import apply_spectra_6

            img = image_service.load_image(full_path)

            # Apply resizing if requested
            if width or height:
                img = image_service.resize_image(
                    img, width, height, preserve_aspect, mode, pad_color
                )

            # Apply E-Ink processing if requested
            # Use ENV defaults if query params not provided
            if eink:
                actual_saturation = saturation if saturation is not None else EINK_SATURATION
                actual_contrast = contrast if contrast is not None else EINK_CONTRAST
                actual_brightness = brightness if brightness is not None else EINK_BRIGHTNESS
                actual_sharpen = sharpen if sharpen is not None else EINK_SHARPEN
                img = apply_spectra_6(img, actual_saturation, actual_contrast, actual_brightness, actual_sharpen)

            # Determine output format
            output_format = format if format else full_path.suffix[1:].lower()
            if output_format not in ['jpeg', 'png', 'webp', 'bmp', 'gif']:
                output_format = 'jpeg'

            # Convert mode if needed for format
            if output_format == 'jpeg' and img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            elif output_format == 'png' and img.mode not in ('RGB', 'RGBA', 'L', 'LA'):
                img = img.convert('RGBA')

            # Save to buffer
            buffer = BytesIO()
            img.save(buffer, format=output_format.upper(), quality=95, optimize=True)

            # Get buffer content for ETag and length (single read)
            content = buffer.getvalue()
            content_length = len(content)
            etag = hashlib.md5(content).hexdigest()
            buffer.seek(0)

            content_type = f"image/{output_format}"
            filename_out = f"{full_path.stem}.{output_format}"

            # Get file mtime for Last-Modified header
            mtime = full_path.stat().st_mtime

            return buffer, content_type, filename_out, mtime, content_length, etag

        buffer, content_type, filename_out, mtime, content_length, etag = await asyncio.get_event_loop().run_in_executor(
            None, process
        )

        # Build Content-Location URI for this specific image
        # RFC 7231: indicates a URI that can be used as an identifier for this resource
        content_location = f"/api/image/{image_path}"

        # Format Last-Modified as RFC 7231 HTTP-date
        last_modified = formatdate(mtime, usegmt=True)

        # Check for conditional request (304 Not Modified)
        if_none_match = request.headers.get("If-None-Match")
        if if_none_match and if_none_match.strip('"') == etag:
            return Response(status_code=304)

        headers = {
            "Content-Length": str(content_length),
            # Disable caching for processed images to allow parameter changes
            "Cache-Control": "no-cache, no-store, must-revalidate" if eink else "public, max-age=86400",
            "Content-Disposition": f'inline; filename="{filename_out}"',
            "Content-Location": content_location,
            "ETag": f'"{etag}"',
            "Last-Modified": last_modified,
            "Vary": "Accept",  # Response varies based on Accept header
        }

        # HEAD request - return headers only, no body (close buffer)
        if request.method == "HEAD":
            buffer.close()
            return Response(status_code=200, headers=headers, media_type=content_type)

        return StreamingResponse(
            iter_buffer_with_cleanup(buffer),
            media_type=content_type,
            headers=headers
        )

    except PathSecurityError:
        return JSONResponse(
            status_code=400,
            content=error_response("Invalid image path")
        )
    except Exception as e:
        logger.error(f"Failed to process image {image_path}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=error_response("Failed to process image")
        )


# =============================================================================
# Sync Group API - Pydantic Models
# =============================================================================

class SyncGroupCreateRequest(BaseModel):
    """Request body for creating a sync group"""
    # Alphanumeric, dash, underscore only (#17)
    group_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique identifier for the group (alphanumeric, dash, underscore)"
    )
    duration: int = Field(30, ge=5, le=3600, description="Cycle duration in seconds (5-3600)")
    # Limit length to prevent DoS (#9)
    folders: Optional[str] = Field(
        None,
        max_length=500,
        description="Comma-separated folder paths (max 500 chars)"
    )
    recursive: bool = Field(False, description="Search subfolders recursively")
    width: Optional[int] = Field(None, ge=1, le=MAX_IMAGE_DIMENSION, description="Target width")
    height: Optional[int] = Field(None, ge=1, le=MAX_IMAGE_DIMENSION, description="Target height")
    preserve_aspect: bool = Field(True, description="Preserve aspect ratio")
    mode: Literal['fit', 'crop'] = Field('fit', description="Resize mode")
    # Validate color format (#16)
    pad_color: str = Field(
        "black",
        max_length=50,
        pattern=r"^([a-zA-Z]+|#[0-9a-fA-F]{3,6}|rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\))$",
        description="Letterbox padding color (name, hex, or rgb())"
    )
    eink: bool = Field(False, description="Apply E-Ink color mapping")
    format: Optional[Literal['jpeg', 'png', 'webp', 'bmp', 'gif']] = Field(None, description="Output format")
    saturation: float = Field(EINK_SATURATION, ge=0.0, le=10.0, description="E-Ink saturation")
    contrast: float = Field(EINK_CONTRAST, ge=0.0, le=5.0, description="E-Ink contrast")
    brightness: float = Field(EINK_BRIGHTNESS, ge=0.0, le=5.0, description="E-Ink brightness")
    sharpen: bool = Field(EINK_SHARPEN, description="Apply sharpening")


class SyncGroupUpdateRequest(BaseModel):
    """Request body for updating a sync group (all fields optional)"""
    duration: Optional[int] = Field(None, ge=5, le=3600, description="Cycle duration in seconds")
    # Limit length to prevent DoS (#9)
    folders: Optional[str] = Field(
        None,
        max_length=500,
        description="Comma-separated folder paths (max 500 chars)"
    )
    recursive: Optional[bool] = Field(None, description="Search subfolders recursively")
    width: Optional[int] = Field(None, ge=1, le=MAX_IMAGE_DIMENSION, description="Target width")
    height: Optional[int] = Field(None, ge=1, le=MAX_IMAGE_DIMENSION, description="Target height")
    preserve_aspect: Optional[bool] = Field(None, description="Preserve aspect ratio")
    mode: Optional[Literal['fit', 'crop']] = Field(None, description="Resize mode")
    # Validate color format (#16)
    pad_color: Optional[str] = Field(
        None,
        max_length=50,
        pattern=r"^([a-zA-Z]+|#[0-9a-fA-F]{3,6}|rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\))$",
        description="Letterbox padding color (name, hex, or rgb())"
    )
    eink: Optional[bool] = Field(None, description="Apply E-Ink color mapping")
    format: Optional[Literal['jpeg', 'png', 'webp', 'bmp', 'gif']] = Field(None, description="Output format")
    saturation: Optional[float] = Field(None, ge=0.0, le=10.0, description="E-Ink saturation")
    contrast: Optional[float] = Field(None, ge=0.0, le=5.0, description="E-Ink contrast")
    brightness: Optional[float] = Field(None, ge=0.0, le=5.0, description="E-Ink brightness")
    sharpen: Optional[bool] = Field(None, description="Apply sharpening")


# =============================================================================
# Sync Group API - Endpoints
# =============================================================================

def get_sync_manager(request: Request) -> SyncManager:
    """Get the SyncManager from app state"""
    return request.app.state.sync_manager


@router.get("/sync/admin", include_in_schema=False)
async def sync_admin_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    """
    Serve the admin dashboard for managing sync groups.

    This HTML page allows administrators to:
    - View all active sync groups
    - Create new sync groups with settings
    - Update group settings (duration, image processing params)
    - Delete groups (disconnects all browsers)

    Requires authentication if OIDC is configured.
    """
    config = get_oidc_config()

    # If OIDC enabled but user not authenticated, redirect to login
    if config.enabled and user is None:
        return RedirectResponse(url="/auth/login?redirect=/sync/admin")

    # Generate CSRF token for the page
    csrf_token = get_csrf_token(request)

    return templates.TemplateResponse(
        "sync_admin.html",
        {
            "request": request,
            "user": user,
            "csrf_token": csrf_token,
        }
    )


@router.get("/sync/admin/groups", responses={
    200: {
        "description": "List of all active sync groups",
        "content": {
            "application/json": {
                "example": [{
                    "group_id": "living-room",
                    "browser_count": 5,
                    "current_cycle": 12,
                    "created_at": "2024-01-07T12:00:00Z",
                    "settings": {"duration": 30, "folders": "earth,space"}
                }]
            }
        }
    },
    401: {"description": "Not authenticated"}
})
async def list_sync_groups(request: Request, user: User = Depends(get_current_user)):
    """
    List all active sync groups with their settings and connected browser count.

    Requires authentication.

    Returns:
        List of sync groups with their configuration and status.
    """
    sync_manager = get_sync_manager(request)
    groups = await sync_manager.list_groups()
    return JSONResponse(content=groups)


@router.post("/sync/admin/groups", responses={
    201: {"description": "Sync group created successfully"},
    400: {"description": "Group already exists or invalid parameters"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"}
})
async def create_sync_group(
    request: Request,
    body: SyncGroupCreateRequest,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Create a new sync group with the specified settings.

    Requires authentication.

    All image processing parameters are configurable:
    - duration: How often images cycle (5-3600 seconds)
    - folders: Which folders to pull images from
    - Image processing: width, height, mode, eink, etc.

    Returns:
        The created sync group configuration.
    """
    sync_manager = get_sync_manager(request)
    logger.info(f"User {user.email} creating sync group: {body.group_id}")

    # Build settings from request body
    settings = SyncGroupSettings(
        duration=body.duration,
        folders=body.folders,
        recursive=body.recursive,
        width=body.width,
        height=body.height,
        preserve_aspect=body.preserve_aspect,
        mode=body.mode,
        pad_color=body.pad_color,
        eink=body.eink,
        format=body.format,
        saturation=body.saturation,
        contrast=body.contrast,
        brightness=body.brightness,
        sharpen=body.sharpen
    )

    try:
        group = await sync_manager.create_group(body.group_id, settings)
        return JSONResponse(
            status_code=201,
            content=group.to_dict()
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/sync/admin/groups/{group_id}", responses={
    200: {"description": "Sync group updated successfully"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"},
    404: {"description": "Group not found"}
})
async def update_sync_group(
    request: Request,
    group_id: str,
    body: SyncGroupUpdateRequest,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Update settings for an existing sync group.

    Requires authentication.

    Any setting can be updated - only provided fields will be changed.
    All connected browsers will receive a SETTINGS_CHANGED message
    and adjust their behavior accordingly.

    Returns:
        The updated sync group configuration.
    """
    sync_manager = get_sync_manager(request)
    logger.info(f"User {user.email} updating sync group: {group_id}")

    # Build updates dict from non-None fields
    updates = {k: v for k, v in body.model_dump().items() if v is not None}

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    group = await sync_manager.update_group_settings(group_id, updates)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    return JSONResponse(content=group.to_dict())


@router.delete("/sync/admin/groups/{group_id}", responses={
    200: {"description": "Sync group deleted"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"},
    404: {"description": "Group not found"}
})
async def delete_sync_group(
    request: Request,
    group_id: str,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Delete a sync group.

    Requires authentication.

    All connected browsers will receive a DISCONNECT message
    and their WebSocket connections will be closed.

    Returns:
        Confirmation of deletion.
    """
    sync_manager = get_sync_manager(request)
    logger.info(f"User {user.email} deleting sync group: {group_id}")
    deleted = await sync_manager.delete_group(group_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    return JSONResponse(content={"deleted": group_id})


@router.get("/sync", include_in_schema=False)
async def sync_page(
    request: Request,
    sync: str = Query(..., description="Sync group ID to join")
):
    """
    Serve the sync display page for a browser to join a sync group.

    The browser connects via WebSocket to the specified sync group
    and displays images synchronized with other browsers in the group.

    Query Parameters:
        sync: The group ID to join (must exist, created via admin API)
    """
    sync_manager = get_sync_manager(request)
    group = await sync_manager.get_group(sync)

    if not group:
        raise HTTPException(
            status_code=404,
            detail=f"Sync group '{sync}' not found. Create it via /sync/admin first."
        )

    # Get WebSocket URL
    ws_scheme = "wss" if request.url.scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{request.url.netloc}/ws/sync/{sync}"

    return templates.TemplateResponse(
        "sync.html",
        {
            "request": request,
            "group_id": sync,
            "ws_url": ws_url
        }
    )


@router.websocket("/ws/sync/{group_id}")
async def websocket_sync(websocket: WebSocket, group_id: str):
    """
    WebSocket endpoint for synchronized image display.

    Protocol:
        Server -> Client:
        - WELCOME: browser_id, group_id, browser_count
        - ASSIGN: image_url, image_id, cycle
        - PRELOAD: image_url, switch_at (timestamp ms)
        - SWITCH: timestamp, cycle
        - SETTINGS_CHANGED: settings updated
        - DISCONNECT: group deleted

        Client -> Server:
        - READY: image loaded
        - PRELOAD_READY: next image preloaded
        - HEARTBEAT: keep-alive
        - SWITCH_COMPLETE: transition done
    """
    sync_manager = websocket.app.state.sync_manager
    browser = None  # Track for cleanup

    # Origin validation (S2) - only if ALLOWED_WS_ORIGINS is configured
    if ALLOWED_WS_ORIGINS:
        origin = websocket.headers.get("origin", "")
        if origin not in ALLOWED_WS_ORIGINS:
            logger.warning(f"WebSocket connection rejected: origin '{origin}' not in allowed list")
            await websocket.close(code=4003, reason="Forbidden origin")
            return

    # Set base URL from first request
    if not sync_manager._base_url:
        base_url = os.environ.get('BASE_URL', '')
        if not base_url:
            scheme = "https" if websocket.url.scheme == "wss" else "http"
            host = websocket.headers.get("host", "localhost")
            base_url = f"{scheme}://{host}"
        sync_manager.set_base_url(base_url)

    # Check if group exists BEFORE accepting (#4)
    group = await sync_manager.get_group(group_id)
    if not group:
        # Close without accepting - client receives close frame
        await websocket.close(code=4004, reason=f"Group '{group_id}' not found")
        return

    # Accept the connection
    await websocket.accept()

    try:
        # Add browser to group (may fail if group deleted or limit reached)
        try:
            browser = await sync_manager.add_browser(group_id, websocket)
        except ValueError as e:
            # Connection limit reached (A10)
            await websocket.close(code=4008, reason=str(e))
            return

        if not browser:
            await websocket.close(code=4004, reason="Failed to join group")
            return

        # Refresh group reference after adding browser
        group = await sync_manager.get_group(group_id)
        if not group:
            await websocket.close(code=4004, reason="Group no longer exists")
            return

        # Send welcome message
        await websocket.send_json({
            "type": "WELCOME",
            "browser_id": browser.browser_id,
            "group_id": group_id,
            "browser_count": len(group.browsers),
            "settings": group.settings.to_dict()
        })

        # Send initial image assignment
        if browser.current_image:
            image_url = sync_manager.build_image_url(
                browser.current_image,
                group.settings
            )
            await websocket.send_json({
                "type": "ASSIGN",
                "image_url": image_url,
                "image_id": browser.current_image,
                "cycle": group.current_cycle
            })

        # Handle incoming messages
        while True:
            try:
                data = await websocket.receive_json()
                await sync_manager.handle_browser_message(
                    group_id,
                    browser.browser_id,
                    data
                )
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError as e:
                # Handle malformed JSON gracefully (#7)
                logger.warning(f"Invalid JSON from browser {browser.browser_id}: {e}")
            except RuntimeError as e:
                # Handle connection state errors (#7)
                logger.warning(f"WebSocket error for {browser.browser_id}: {e}")
                break
            except Exception as e:
                # Log unexpected errors but keep connection alive (#7)
                logger.error(f"Unexpected error handling message: {e}", exc_info=True)

    finally:
        # Clean up browser state
        if browser:
            try:
                await sync_manager.remove_browser(group_id, browser.browser_id)
                logger.info(f"Browser {browser.browser_id} disconnected from {group_id}")
            except Exception as e:
                logger.error(f"Error during browser cleanup: {e}")


# =============================================================================
# Catch-all route (MUST be last)
# =============================================================================

@router.api_route("/{folder_path:path}", methods=["GET", "HEAD"], responses={
    200: {"description": "Random image or web interface"},
    304: {"description": "Not Modified (conditional request)"},
    400: {"description": "Invalid folder path"},
    404: {"description": "No images found"}
})
async def root(
    request: Request,
    folder_path: str,
    api: Optional[str] = Query(None, description="API mode - 'random-image' for image endpoint"),
    width: Optional[int] = Query(None, description=f"Target width in pixels (max {MAX_IMAGE_DIMENSION})", ge=1, le=MAX_IMAGE_DIMENSION),
    height: Optional[int] = Query(None, description=f"Target height in pixels (max {MAX_IMAGE_DIMENSION})", ge=1, le=MAX_IMAGE_DIMENSION),
    preserve_aspect: bool = Query(True, description="Preserve aspect ratio when resizing"),
    mode: Literal['fit', 'crop'] = Query('fit', description="Resize mode - 'fit' or 'crop'"),
    pad_color: str = Query("black", description="Letterbox padding color (CSS: name, hex, rgb())"),
    eink: bool = Query(False, description="Apply Spectra-6 E-Ink color mapping"),
    format: Optional[Literal['jpeg', 'png', 'webp', 'bmp', 'gif']] = Query(None, description="Output format (defaults to original format)"),
    saturation: Optional[float] = Query(None, description=f"E-Ink saturation multiplier (default: {EINK_SATURATION})", ge=0.0, le=10.0),
    contrast: Optional[float] = Query(None, description=f"E-Ink contrast multiplier (default: {EINK_CONTRAST})", ge=0.0, le=5.0),
    brightness: Optional[float] = Query(None, description=f"E-Ink brightness multiplier (default: {EINK_BRIGHTNESS})", ge=0.0, le=5.0),
    sharpen: Optional[bool] = Query(None, description=f"Apply sharpening for E-Ink preprocessing (default: {EINK_SHARPEN})"),
    recursive: bool = Query(False, description="Recursively search subfolders (default: false)"),
    folders: Optional[str] = Query(None, description="Comma-separated list of folder paths (alternative to path)"),
    also: Optional[str] = Query(None, description="Comma-separated list of additional folders (use with path)"),
    refresh: Optional[int] = Query(None, ge=5, le=3600, description="Auto-refresh interval in seconds (5-3600) for cycling through images")
):
    """
    Main endpoint - serves either the web interface or random image API.

    Supports path-based folder navigation:
    - / - Root directory (top-level images only, unless recursive=true)
    - /earth - Images from earth folder (non-recursive)
    - /earth/folder1 - Images from earth/folder1 (non-recursive)

    Query Parameters:
    - api: Set to 'random-image' for API mode
    - width: Target width for resizing (optional, max from MAX_IMAGE_DIMENSION env)
    - height: Target height for resizing (optional, max from MAX_IMAGE_DIMENSION env)
    - preserve_aspect: Whether to preserve aspect ratio (default: true)
    - mode: Resize mode - 'fit' (letterbox) or 'crop' (fill, default: fit)
    - eink: Apply Spectra-6 dithering for E-Ink displays (default: false)
    - format: Output format - 'jpeg', 'png', 'webp', 'bmp', 'gif' (defaults to original)
    - saturation: E-Ink saturation multiplier 0.0-10.0 (default from EINK_SATURATION env)
    - contrast: E-Ink contrast multiplier 0.0-5.0 (default from EINK_CONTRAST env)
    - brightness: E-Ink brightness multiplier 0.0-5.0 (default from EINK_BRIGHTNESS env)
    - sharpen: Apply sharpening for E-Ink (default from EINK_SHARPEN env)
    - recursive: Recursively search subfolders (default: false)
    - folders: Comma-separated folder paths (e.g., folders=earth,space)
    - also: Additional folders when using path (e.g., /earth?also=space,family)
    - refresh: Auto-refresh interval in seconds (5-3600) for auto-cycling mode

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
    - GET /earth?refresh=30 - Auto-cycle through earth folder every 30 seconds
    - GET /earth?refresh=30&eink=true&width=800 - Auto-cycle with processing
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
            # Use ENV defaults for E-Ink params if not provided
            actual_saturation = saturation if saturation is not None else EINK_SATURATION
            actual_contrast = contrast if contrast is not None else EINK_CONTRAST
            actual_brightness = brightness if brightness is not None else EINK_BRIGHTNESS
            actual_sharpen = sharpen if sharpen is not None else EINK_SHARPEN

            # Run CPU-intensive image processing in thread pool to avoid blocking event loop
            result = await asyncio.get_event_loop().run_in_executor(
                None,  # Use default ThreadPoolExecutor
                image_service.process_image,
                width,
                height,
                preserve_aspect,
                mode,
                pad_color,
                eink,
                format,
                actual_saturation,
                actual_contrast,
                actual_brightness,
                actual_sharpen,
                final_folder_paths,
                recursive
            )

            if result is None:
                return JSONResponse(
                    status_code=404,
                    content=error_response("No images found")
                )
        except PathSecurityError as e:
            # Security violation - return 400 Bad Request with generic message
            return JSONResponse(
                status_code=400,
                content=error_response("Invalid folder path")
            )

        buffer, content_type, filename, relative_path, mtime = result

        # Get buffer content for ETag and length (single read, more efficient)
        content = buffer.getvalue()
        content_length = len(content)
        etag = hashlib.md5(content).hexdigest()
        buffer.seek(0)

        # Build Content-Location URI for re-requesting this specific image
        # RFC 7231: indicates a URI that can be used as an identifier for this resource
        content_location = f"/api/image/{relative_path}"

        # Format Last-Modified as RFC 7231 HTTP-date
        last_modified = formatdate(mtime, usegmt=True)

        # Check for conditional request (304 Not Modified)
        if_none_match = request.headers.get("If-None-Match")
        if if_none_match and if_none_match.strip('"') == etag:
            buffer.close()
            return Response(status_code=304)

        headers = {
            "Content-Length": str(content_length),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Location": content_location,
            "ETag": f'"{etag}"',
            "Last-Modified": last_modified,
            "Vary": "Accept",  # Response varies based on Accept header
        }

        # HEAD request - return headers only, no body (close buffer)
        if request.method == "HEAD":
            buffer.close()
            return Response(status_code=200, headers=headers, media_type=content_type)

        return StreamingResponse(
            iter_buffer_with_cleanup(buffer),
            media_type=content_type,
            headers=headers
        )

    # Default mode - serve web interface
    else:
        try:
            # Get base URL from environment or use request host
            base_url = os.environ.get('BASE_URL', '')
            if not base_url:
                # Construct from request
                base_url = f"{request.url.scheme}://{request.url.netloc}"

            if refresh:
                # Refresh mode: single random image with processing support
                image_path = image_service.get_random_image_path(final_folder_paths, recursive)
                if not image_path:
                    raise HTTPException(status_code=404, detail="No images found")

                # Build API URL with all processing params
                relative_path = image_path.relative_to(image_service.image_dir)
                params = []
                if width:
                    params.append(f"width={width}")
                if height:
                    params.append(f"height={height}")
                if not preserve_aspect:
                    params.append("preserve_aspect=false")
                if mode != 'fit':
                    params.append(f"mode={mode}")
                if pad_color != "black":
                    params.append(f"pad_color={pad_color}")
                if eink:
                    params.append("eink=true")
                if format:
                    params.append(f"format={format}")
                if saturation is not None:
                    params.append(f"saturation={saturation}")
                if contrast is not None:
                    params.append(f"contrast={contrast}")
                if brightness is not None:
                    params.append(f"brightness={brightness}")
                if sharpen is not None:
                    params.append(f"sharpen={str(sharpen).lower()}")

                query_string = "&".join(params) if params else ""
                image_url = f"{base_url}/api/image/{relative_path}"
                if query_string:
                    image_url += f"?{query_string}"

                return templates.TemplateResponse(
                    "index.html",
                    {
                        "request": request,
                        "image_url": image_url,
                        "refresh": refresh
                    }
                )
            else:
                # Original slideshow mode: all images for JavaScript rotation
                image_files = image_service.get_image_files(final_folder_paths, recursive)

                # Build image URLs
                image_urls = [f"{base_url}/images/{file.relative_to(image_service.image_dir)}" for file in image_files]

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
