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
from typing import Optional, Literal, Dict, Any, List
from urllib.parse import unquote
from fastapi import APIRouter, Query, HTTPException, Path as PathParam, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, Response, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from datetime import datetime, timezone
from app.services.image_service import ImageService
from app.services.sync_manager import (
    DisplayManager,
    DisplayGroupSettings,
    DisplayEndpoint,
    WebhookConfig,
    WebhookTrigger,
    WebhookPayloadOption,
)
from app.services.immich_provider import ImmichProvider, ImmichPullConfig
from app.services.homeassistant_provider import HomeAssistantProvider
from app.auth.dependencies import get_current_user, get_optional_user, User, get_csrf_token, verify_csrf_token
from app.auth.oidc import get_oidc_config
from app.exceptions import PathSecurityError, NoImagesFoundError, error_response
from app.utils.path_security import resolve_image_path
from app.utils.logging import logger
from app.utils.metrics import metrics
from app.utils.blur import create_blur_background, should_skip_blur

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


# =============================================================================
# Clock and Weather Endpoints (for overlay features)
# =============================================================================

@router.get("/api/clock", responses={
    200: {
        "description": "Current time from specified source",
        "content": {
            "application/json": {
                "example": {"datetime": "2024-01-15T14:30:00+00:00", "source": "server"}
            }
        }
    }
})
async def get_clock_data(
    request: Request,
    source: str = Query("client", description="Time source: client, server, or homeassistant")
):
    """
    Get current time from specified source.

    - **client**: Returns minimal response, client uses its own time
    - **server**: Returns server's system time
    - **homeassistant**: Fetches time from Home Assistant

    The client uses this for offset-based time sync to ensure
    accurate time display regardless of client clock drift.
    """
    if source == "homeassistant":
        ha_provider = getattr(request.app.state, 'ha_provider', None)
        if ha_provider:
            result = await ha_provider.get_time()
            if "error" not in result:
                return result
            # Fall back to server time on error
            logger.warning(f"HA time fetch failed, falling back to server: {result.get('error')}")

        # Fall back to server time if HA not configured or failed
        return {
            "datetime": datetime.now(timezone.utc).isoformat(),
            "source": "server"
        }

    elif source == "server":
        # Return server's system time
        return {
            "datetime": datetime.now(timezone.utc).isoformat(),
            "source": "server"
        }

    else:
        # Client mode - just acknowledge, client handles its own time
        return {"source": "client"}


@router.get("/api/weather", responses={
    200: {
        "description": "Weather data from Home Assistant",
        "content": {
            "application/json": {
                "example": {
                    "condition": "sunny",
                    "temperature": 22,
                    "temperature_unit": "°C",
                    "humidity": 65,
                    "friendly_name": "Home",
                    "forecast": []
                }
            }
        }
    },
    503: {"description": "Home Assistant not available"}
})
async def get_weather_data(
    request: Request,
    entity_id: str = Query(..., description="Home Assistant weather entity ID (e.g., weather.home)")
):
    """
    Get weather data from Home Assistant.

    Fetches current conditions and optional forecast from a Home Assistant
    weather entity. Results are cached for 10 minutes.
    """
    ha_provider = getattr(request.app.state, 'ha_provider', None)
    if not ha_provider:
        raise HTTPException(
            status_code=503,
            detail="Home Assistant not configured. Set HOMEASSISTANT_URL and HOMEASSISTANT_TOKEN environment variables."
        )

    data = await ha_provider.get_weather(entity_id)
    if "error" in data:
        raise HTTPException(status_code=503, detail=data["error"])

    return data


@router.get("/api/weather/forecast", responses={
    200: {
        "description": "Weather forecast from Home Assistant",
        "content": {
            "application/json": {
                "example": {
                    "forecast_type": "daily",
                    "forecast": [
                        {
                            "datetime": "2026-01-22T00:00:00+00:00",
                            "condition": "sunny",
                            "temperature": 22,
                            "templow": 15,
                            "precipitation_probability": 10
                        }
                    ]
                }
            }
        }
    },
    503: {"description": "Home Assistant not available"}
})
async def get_weather_forecast(
    request: Request,
    entity_id: str = Query(..., description="Home Assistant weather entity ID (e.g., weather.home)"),
    forecast_type: str = Query("daily", description="Forecast type: daily, hourly, or twice_daily"),
    limit: int = Query(3, ge=1, le=24, description="Number of forecast entries to return")
):
    """
    Get weather forecast from Home Assistant.

    Fetches forecast data using the weather.get_forecasts service.
    Supports daily, hourly, or twice_daily forecasts.
    Results are cached for 10 minutes.
    """
    ha_provider = getattr(request.app.state, 'ha_provider', None)
    if not ha_provider:
        raise HTTPException(
            status_code=503,
            detail="Home Assistant not configured. Set HOMEASSISTANT_URL and HOMEASSISTANT_TOKEN environment variables."
        )

    data = await ha_provider.get_forecast(entity_id, forecast_type, limit)
    if "error" in data:
        raise HTTPException(status_code=503, detail=data["error"])

    return data


@router.get("/api/homeassistant/test", responses={
    200: {"description": "Connection test result"},
    503: {"description": "Home Assistant not available"}
})
async def test_homeassistant_connection(request: Request):
    """
    Test connection to Home Assistant.

    Returns success status and any error messages.
    """
    ha_provider = getattr(request.app.state, 'ha_provider', None)
    if not ha_provider:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Home Assistant not configured"}
        )

    result = await ha_provider.test_connection()
    if not result.get("success"):
        return JSONResponse(status_code=503, content=result)

    return result


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
    for monitoring display group and WebSocket connection health.
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


@router.get("/api/blur/{image_path:path}", responses={
    200: {"description": "Blur background data URL", "content": {"application/json": {}}},
    400: {"description": "Invalid image path"},
    404: {"description": "Image not found"}
})
async def get_blur_background(
    image_path: str,
    blur_amount: int = Query(50, ge=0, le=100, description="Blur intensity (0-100)")
):
    """
    Get a blurred version of an image as a base64 data URL.

    Used for creating blurred background behind letterboxed images.
    The blur is calculated based on image dimensions for consistent
    appearance across different image sizes.

    Returns JSON with a 'blur_url' field containing the data URL.
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

        # Generate blur in thread pool (CPU-intensive)
        def generate_blur():
            img = image_service.load_image(full_path)
            return create_blur_background(img, blur_amount=blur_amount)

        blur_url = await asyncio.get_event_loop().run_in_executor(None, generate_blur)

        return JSONResponse(content={"blur_url": blur_url})

    except PathSecurityError:
        return JSONResponse(
            status_code=400,
            content=error_response("Invalid image path")
        )
    except Exception as e:
        logger.error(f"Failed to generate blur for {image_path}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=error_response("Failed to generate blur")
        )


# =============================================================================
# Display Group API - Pydantic Models
# =============================================================================

class DisplayGroupCreateRequest(BaseModel):
    """Request body for creating a display group"""
    # Alphanumeric, dash, underscore only (#17)
    group_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique identifier for the group (alphanumeric, dash, underscore)"
    )
    duration: int = Field(30, ge=5, le=3600, description="Cycle duration in seconds (5-3600)")
    auto_cycle: bool = Field(True, description="Automatic image cycling (False = on-demand mode)")
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
    # Clock settings
    show_time: bool = Field(False, description="Show time overlay")
    show_date: bool = Field(False, description="Show date overlay")
    time_format: Literal['12', '24'] = Field('24', description="Time format (12/24 hour)")
    date_format: str = Field('DDDD, MMMM DD', max_length=100, description="Date format (Immich Kiosk tokens)")
    clock_source: Literal['client', 'server', 'homeassistant'] = Field('client', description="Clock time source")
    clock_position: Literal['bottom-left', 'bottom-right', 'top-left', 'top-right'] = Field('bottom-left', description="Clock position")
    overlay_theme: Literal['fade', 'solid', 'bubble', 'blur'] = Field('fade', description="Overlay theme style")
    # Weather settings
    show_weather: bool = Field(False, description="Show weather overlay")
    weather_position: Literal['bottom-left', 'bottom-right', 'top-left', 'top-right'] = Field('bottom-right', description="Weather position")
    weather_ha_entity: Optional[str] = Field(None, max_length=100, description="Home Assistant weather entity ID")
    weather_show_forecast: bool = Field(False, description="Show weather forecast")
    # Layout settings
    layout: Literal['single', 'splitview', 'splitview-landscape'] = Field('single', description="Display layout")
    # Background blur settings
    background_blur: bool = Field(False, description="Enable background blur")
    background_blur_amount: int = Field(50, ge=0, le=100, description="Blur intensity (0-100)")
    image_fit: Literal['contain', 'cover'] = Field('contain', description="Image fit mode")
    # Burn-in prevention settings
    burn_in_protection: bool = Field(False, description="Enable burn-in protection")
    burn_in_interval: int = Field(5, ge=1, le=60, description="Dimming interval (minutes)")
    burn_in_duration: int = Field(30, ge=5, le=120, description="Dimming duration (seconds)")
    burn_in_opacity: int = Field(70, ge=10, le=90, description="Dimmed opacity (%)")
    burn_in_shift_enabled: bool = Field(True, description="Enable pixel shifting")
    burn_in_shift_amount: int = Field(5, ge=1, le=20, description="Shift amount (pixels)")
    burn_in_shift_interval: int = Field(60, ge=10, le=300, description="Shift interval (seconds)")
    # Webhook settings
    webhooks: List[WebhookConfig] = Field(default_factory=list, description="Webhook configurations")
    # Endpoints to create with the group (optional)
    endpoints: Optional[List["EndpointCreateRequest"]] = Field(
        default=None,
        description="Endpoints to create with the group"
    )


class DisplayGroupUpdateRequest(BaseModel):
    """Request body for updating a display group (all fields optional)"""
    duration: Optional[int] = Field(None, ge=5, le=3600, description="Cycle duration in seconds")
    auto_cycle: Optional[bool] = Field(None, description="Automatic image cycling (False = on-demand mode)")
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
    # Clock settings
    show_time: Optional[bool] = Field(None, description="Show time overlay")
    show_date: Optional[bool] = Field(None, description="Show date overlay")
    time_format: Optional[Literal['12', '24']] = Field(None, description="Time format (12/24 hour)")
    date_format: Optional[str] = Field(None, max_length=100, description="Date format (Immich Kiosk tokens)")
    clock_source: Optional[Literal['client', 'server', 'homeassistant']] = Field(None, description="Clock time source")
    clock_position: Optional[Literal['bottom-left', 'bottom-right', 'top-left', 'top-right']] = Field(None, description="Clock position")
    overlay_theme: Optional[Literal['fade', 'solid', 'bubble', 'blur']] = Field(None, description="Overlay theme style")
    # Weather settings
    show_weather: Optional[bool] = Field(None, description="Show weather overlay")
    weather_position: Optional[Literal['bottom-left', 'bottom-right', 'top-left', 'top-right']] = Field(None, description="Weather position")
    weather_ha_entity: Optional[str] = Field(None, max_length=100, description="Home Assistant weather entity ID")
    weather_show_forecast: Optional[bool] = Field(None, description="Show weather forecast")
    weather_forecast_type: Optional[Literal['daily', 'hourly', 'twice_daily']] = Field(None, description="Forecast type")
    weather_forecast_days: Optional[int] = Field(None, ge=1, le=24, description="Number of forecast entries")
    weather_show_high_low: Optional[bool] = Field(None, description="Show high/low temperatures")
    weather_show_precipitation: Optional[bool] = Field(None, description="Show precipitation probability")
    # Layout settings
    layout: Optional[Literal['single', 'splitview', 'splitview-landscape']] = Field(None, description="Display layout")
    # Background blur settings
    background_blur: Optional[bool] = Field(None, description="Enable background blur")
    background_blur_amount: Optional[int] = Field(None, ge=0, le=100, description="Blur intensity (0-100)")
    image_fit: Optional[Literal['contain', 'cover']] = Field(None, description="Image fit mode")
    # Burn-in prevention settings
    burn_in_protection: Optional[bool] = Field(None, description="Enable burn-in protection")
    burn_in_interval: Optional[int] = Field(None, ge=1, le=60, description="Dimming interval (minutes)")
    burn_in_duration: Optional[int] = Field(None, ge=5, le=120, description="Dimming duration (seconds)")
    burn_in_opacity: Optional[int] = Field(None, ge=10, le=90, description="Dimmed opacity (%)")
    burn_in_shift_enabled: Optional[bool] = Field(None, description="Enable pixel shifting")
    burn_in_shift_amount: Optional[int] = Field(None, ge=1, le=20, description="Shift amount (pixels)")
    burn_in_shift_interval: Optional[int] = Field(None, ge=10, le=300, description="Shift interval (seconds)")
    # Webhook settings
    webhooks: Optional[List[WebhookConfig]] = Field(None, description="Webhook configurations")


# =============================================================================
# Display Group API - Endpoints
# =============================================================================

def get_display_manager(request: Request) -> DisplayManager:
    """Get the DisplayManager from app state"""
    return request.app.state.display_manager


@router.get("/admin", include_in_schema=False)
async def admin_page(request: Request, user: Optional[User] = Depends(get_optional_user)):
    """
    Serve the unified admin dashboard.

    This HTML page provides tabbed access to:
    - Display Groups: Manage synchronized browser displays
    - Immich: Configure and trigger image pulls from Immich servers

    Requires authentication if OIDC is configured.
    """
    config = get_oidc_config()

    # If OIDC enabled but user not authenticated, redirect to login
    if config.enabled and user is None:
        return RedirectResponse(url="/auth/login?redirect=/admin")

    # Generate CSRF token for the page
    csrf_token = get_csrf_token(request)

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": user,
            "csrf_token": csrf_token,
        }
    )


@router.get("/display/admin", include_in_schema=False)
async def display_admin_redirect():
    """Redirect display admin URL to unified admin page."""
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/api/display/groups", responses={
    200: {
        "description": "List of all active display groups",
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
async def list_display_groups(request: Request, user: User = Depends(get_current_user)):
    """
    List all active display groups with their settings and connected browser count.

    Requires authentication.

    Returns:
        List of display groups with their configuration and status.
    """
    display_manager = get_display_manager(request)
    groups = await display_manager.list_groups()
    return JSONResponse(content=groups)


@router.post("/api/display/groups", responses={
    201: {"description": "Sync group created successfully"},
    400: {"description": "Group already exists or invalid parameters"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"}
})
async def create_display_group(
    request: Request,
    body: DisplayGroupCreateRequest,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Create a new display group with the specified settings.

    Requires authentication.

    All image processing parameters are configurable:
    - duration: How often images cycle (5-3600 seconds)
    - folders: Which folders to pull images from
    - Image processing: width, height, mode, eink, etc.

    Returns:
        The created display group configuration.
    """
    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} creating display group: {body.group_id}")

    # Build settings from request body
    settings = DisplayGroupSettings(
        duration=body.duration,
        auto_cycle=body.auto_cycle,
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
        sharpen=body.sharpen,
        # Clock settings
        show_time=body.show_time,
        show_date=body.show_date,
        time_format=body.time_format,
        date_format=body.date_format,
        clock_source=body.clock_source,
        clock_position=body.clock_position,
        overlay_theme=body.overlay_theme,
        # Weather settings
        show_weather=body.show_weather,
        weather_position=body.weather_position,
        weather_ha_entity=body.weather_ha_entity,
        weather_show_forecast=body.weather_show_forecast,
        # Layout settings
        layout=body.layout,
        # Blur settings
        background_blur=body.background_blur,
        background_blur_amount=body.background_blur_amount,
        image_fit=body.image_fit,
    )

    try:
        group = await display_manager.create_group(body.group_id, settings)

        # Create any endpoints specified in the request
        if body.endpoints:
            for ep in body.endpoints:
                if ep.endpoint_id not in group.endpoints:
                    endpoint = DisplayEndpoint(
                        endpoint_id=ep.endpoint_id,
                        name=ep.name,
                        is_persistent=True,
                    )
                    group.endpoints[ep.endpoint_id] = endpoint
            # Save config to persist the endpoints
            display_manager.save_config()

        return JSONResponse(
            status_code=201,
            content=group.to_dict()
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/api/display/groups/{group_id}", responses={
    200: {"description": "Sync group updated successfully"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"},
    404: {"description": "Group not found"}
})
async def update_display_group(
    request: Request,
    group_id: str,
    body: DisplayGroupUpdateRequest,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Update settings for an existing display group.

    Requires authentication.

    Any setting can be updated - only provided fields will be changed.
    All connected browsers will receive a SETTINGS_CHANGED message
    and adjust their behavior accordingly.

    Returns:
        The updated display group configuration.
    """
    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} updating display group: {group_id}")

    # Build updates dict from non-None fields
    updates = {k: v for k, v in body.model_dump().items() if v is not None}

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    group = await display_manager.update_group_settings(group_id, updates)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    return JSONResponse(content=group.to_dict())


@router.delete("/api/display/groups/{group_id}", responses={
    200: {"description": "Sync group deleted"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"},
    404: {"description": "Group not found"}
})
async def delete_display_group(
    request: Request,
    group_id: str,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Delete a display group.

    Requires authentication.

    All connected browsers will receive a DISCONNECT message
    and their WebSocket connections will be closed.

    Returns:
        Confirmation of deletion.
    """
    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} deleting display group: {group_id}")
    deleted = await display_manager.delete_group(group_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    return JSONResponse(content={"deleted": group_id})


@router.post("/api/display/save-config", responses={
    200: {"description": "Configuration saved"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"}
})
async def save_display_config(
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token),
    display_manager: DisplayManager = Depends(get_display_manager)
):
    """
    Save current display groups configuration to file.

    Saves all display groups and their settings to the TOML config file.
    Groups will be automatically restored on application restart.
    """
    logger.info(f"User {user.email} saving display config")
    saved = display_manager.save_config()
    return JSONResponse(content={"saved": saved, "message": f"Saved {saved} groups to config"})


# --- Sync Group Playback Controls ---

@router.post("/api/display/groups/{group_id}/pause", responses={
    200: {"description": "Group paused"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"},
    404: {"description": "Group not found"}
})
async def pause_display_group(
    request: Request,
    group_id: str,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Pause a display group's image cycling.

    Requires authentication.
    Connected browsers will stay on their current image until resumed.
    """
    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} pausing display group: {group_id}")
    success = await display_manager.pause_group(group_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    return JSONResponse(content={"status": "paused", "group_id": group_id})


@router.post("/api/display/groups/{group_id}/play", responses={
    200: {"description": "Group resumed"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"},
    404: {"description": "Group not found"}
})
async def play_display_group(
    request: Request,
    group_id: str,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Resume a display group's image cycling.

    Requires authentication.
    """
    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} resuming display group: {group_id}")
    success = await display_manager.play_group(group_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    return JSONResponse(content={"status": "playing", "group_id": group_id})


@router.post("/api/display/groups/{group_id}/next", responses={
    200: {"description": "Skipped to next image"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"},
    404: {"description": "Group not found or no browsers connected"}
})
async def next_display_group(
    request: Request,
    group_id: str,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Skip to the next image immediately.

    Requires authentication.
    All browsers advance to their next image.
    """
    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} skipping display group: {group_id}")
    success = await display_manager.skip_group(group_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found or no browsers connected")

    return JSONResponse(content={"status": "skipped", "group_id": group_id})


@router.post("/api/display/groups/{group_id}/previous", responses={
    200: {"description": "Went to previous image"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"},
    404: {"description": "Group not found or no history available"}
})
async def previous_display_group(
    request: Request,
    group_id: str,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Go to the previous image.

    Requires authentication.
    Each browser goes back to its own previous image from history.
    Can be called multiple times to go further back (up to 100 images).
    """
    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} going to previous for display group: {group_id}")
    success = await display_manager.previous_group(group_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found or no history available")

    return JSONResponse(content={"status": "rewound", "group_id": group_id})


@router.post("/api/display/groups/{group_id}/rewind-all")
async def rewind_all_group(
    group_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Jump to the first image in history (rewind all the way).

    Requires authentication.
    This is a bulk operation - moves instantly to the beginning of history
    without stepping through each image.
    """
    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} rewinding all for display group: {group_id}")
    success = await display_manager.rewind_all_group(group_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found or no history available")

    return JSONResponse(content={"status": "rewound_all", "group_id": group_id})


@router.post("/api/display/groups/{group_id}/forward-all")
async def forward_all_group(
    group_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Jump to the most recent image (forward all the way).

    Requires authentication.
    This is a bulk operation - moves instantly to the most recent image
    without stepping through each one.
    """
    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} forwarding all for display group: {group_id}")
    success = await display_manager.forward_all_group(group_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found or no forward history available")

    return JSONResponse(content={"status": "forwarded_all", "group_id": group_id})


@router.post("/api/display/groups/{group_id}/refresh", responses={
    200: {"description": "Image list refreshed"},
    401: {"description": "Not authenticated"},
    404: {"description": "Group not found"}
})
async def refresh_group_images(
    group_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Refresh the image list for a display group after files have changed.

    Requires authentication.
    Call this after adding/removing files to the group's image folders
    to update the available images without recreating the group.
    """
    display_manager = get_display_manager(request)
    count = await display_manager.refresh_group_images(group_id)

    if count is None:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    logger.info(f"User {user.email} refreshed images for group '{group_id}': {count} images")
    return JSONResponse(content={"group_id": group_id, "image_count": count})


@router.post("/api/display/groups/{group_id}/browsers/{browser_id}/jump")
async def jump_to_image(
    group_id: str,
    browser_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Jump a specific browser to a specific image in its history.

    Requires authentication.
    The image must be in the browser's image_history or forward_history.
    """
    body = await request.json()
    image_path = body.get("image_path")

    if not image_path:
        raise HTTPException(status_code=400, detail="image_path required")

    display_manager = get_display_manager(request)
    logger.info(f"User {user.email} jumping browser {browser_id} in group {group_id} to {image_path}")
    success = await display_manager.jump_to_image(group_id, browser_id, image_path)

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Group, browser, or image not found in history"
        )

    return JSONResponse(content={"status": "jumped", "image_path": image_path})


@router.post("/api/display/groups/{group_id}/webhooks/{webhook_id}/test")
async def test_webhook(
    group_id: str,
    webhook_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Test a specific webhook by sending a test payload.

    Requires authentication. Returns the webhook response status.
    """
    import httpx
    import hashlib
    import hmac
    import json
    from datetime import datetime, timezone

    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)

    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    # Find the webhook
    webhook = next(
        (wh for wh in group.settings.webhooks if wh.id == webhook_id),
        None
    )
    if not webhook:
        raise HTTPException(status_code=404, detail=f"Webhook '{webhook_id}' not found")

    # Build test payload
    payload = {
        "group_id": group_id,
        "event_type": "test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "This is a test webhook from the screensaver admin"
    }

    # Build headers
    headers = {"Content-Type": "application/json"}
    headers.update(webhook.headers)

    # Add HMAC signature if secret is configured
    if webhook.secret:
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        signature = hmac.new(
            webhook.secret.encode(),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()
        headers["X-Webhook-Signature"] = f"sha256={signature}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook.url, json=payload, headers=headers)
            return JSONResponse(content={
                "status": "sent",
                "response_status": response.status_code,
                "response_body": response.text[:500] if response.text else None
            })
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"status": "timeout", "error": "Webhook request timed out"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "error": str(e)}
        )


# --- Display Endpoint Management APIs ---

class EndpointCreateRequest(BaseModel):
    """Request body for creating a display endpoint"""
    endpoint_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique identifier for the endpoint"
    )
    name: Optional[str] = Field(None, max_length=100, description="Human-readable name")


class EndpointUpdateRequest(BaseModel):
    """Request body for updating a display endpoint"""
    name: Optional[str] = Field(None, max_length=100, description="Human-readable name")
    is_persistent: Optional[bool] = Field(None, description="Promote anonymous to persistent")


@router.get("/api/display/groups/{group_id}/endpoints", responses={
    200: {"description": "List of endpoints for the group"},
    404: {"description": "Group not found"},
})
async def list_endpoints(
    group_id: str = PathParam(...),
    request: Request = None,
    user: User = Depends(get_current_user)
):
    """List all endpoints for a display group."""
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    return JSONResponse(
        content={"endpoints": [e.to_dict() for e in group.endpoints.values()]}
    )


@router.post("/api/display/groups/{group_id}/endpoints", responses={
    201: {"description": "Endpoint created successfully"},
    400: {"description": "Invalid request or endpoint already exists"},
    404: {"description": "Group not found"},
})
async def create_endpoint(
    group_id: str = PathParam(...),
    body: EndpointCreateRequest = None,
    request: Request = None,
    user: User = Depends(get_current_user)
):
    """Create a new persistent endpoint for a display group."""
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    if body.endpoint_id in group.endpoints:
        raise HTTPException(status_code=400, detail=f"Endpoint '{body.endpoint_id}' already exists")

    endpoint = DisplayEndpoint(
        endpoint_id=body.endpoint_id,
        name=body.name,
        is_persistent=True,
    )
    group.endpoints[body.endpoint_id] = endpoint

    # Save config to persist the new endpoint
    display_manager.save_config()

    # Notify admin clients of the change
    await display_manager._notify_admins({
        "type": "GROUP_UPDATED",
        "group": group.to_dict()
    })

    logger.info(f"User {user.email} created endpoint '{body.endpoint_id}' in group '{group_id}'")

    return JSONResponse(
        status_code=201,
        content=endpoint.to_dict()
    )


@router.get("/api/display/groups/{group_id}/endpoints/{endpoint_id}", responses={
    200: {"description": "Endpoint details"},
    404: {"description": "Group or endpoint not found"},
})
async def get_endpoint(
    group_id: str = PathParam(...),
    endpoint_id: str = PathParam(...),
    request: Request = None,
    user: User = Depends(get_current_user)
):
    """Get details of a specific endpoint."""
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    endpoint = group.endpoints.get(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    return JSONResponse(content=endpoint.to_dict())


@router.patch("/api/display/groups/{group_id}/endpoints/{endpoint_id}", responses={
    200: {"description": "Endpoint updated successfully"},
    404: {"description": "Group or endpoint not found"},
})
async def update_endpoint(
    group_id: str = PathParam(...),
    endpoint_id: str = PathParam(...),
    body: EndpointUpdateRequest = None,
    request: Request = None,
    user: User = Depends(get_current_user)
):
    """Update an endpoint (e.g., rename or promote from anonymous to persistent)."""
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    endpoint = group.endpoints.get(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    was_persistent = endpoint.is_persistent
    if body.name is not None:
        endpoint.name = body.name
    if body.is_persistent is not None:
        endpoint.is_persistent = body.is_persistent

    # Save config if endpoint is now persistent (includes promotions)
    if endpoint.is_persistent:
        display_manager.save_config()

    # Notify admin clients of the change
    await display_manager._notify_admins({
        "type": "GROUP_UPDATED",
        "group": group.to_dict()
    })

    logger.info(f"User {user.email} updated endpoint '{endpoint_id}' in group '{group_id}'")

    return JSONResponse(content=endpoint.to_dict())


@router.delete("/api/display/groups/{group_id}/endpoints/{endpoint_id:path}", responses={
    200: {"description": "Endpoint deleted"},
    400: {"description": "Cannot delete - browser connected"},
    404: {"description": "Group or endpoint not found"},
})
async def delete_endpoint(
    group_id: str = PathParam(...),
    endpoint_id: str = PathParam(...),
    request: Request = None,
    user: User = Depends(get_current_user)
):
    """Delete an endpoint. Cannot delete if a browser is currently connected."""
    # Decode URL-encoded endpoint ID (supports "/" characters)
    endpoint_id = unquote(endpoint_id)
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    endpoint = group.endpoints.get(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    # Check if any browser is using this endpoint
    connected_browsers = [b for b in group.browsers.values() if b.endpoint_id == endpoint_id]
    if connected_browsers:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete endpoint '{endpoint_id}' - {len(connected_browsers)} browser(s) connected"
        )

    was_persistent = endpoint.is_persistent
    del group.endpoints[endpoint_id]

    # Save config if the deleted endpoint was persistent
    if was_persistent:
        display_manager.save_config()

    # Notify admin clients of the change
    await display_manager._notify_admins({
        "type": "GROUP_UPDATED",
        "group": group.to_dict()
    })

    logger.info(f"User {user.email} deleted endpoint '{endpoint_id}' from group '{group_id}'")

    return JSONResponse(content={"status": "deleted", "endpoint_id": endpoint_id})


@router.delete("/api/display/groups/{group_id}/browsers/{browser_id}/history/{image_path:path}", responses={
    200: {"description": "Image removed from history"},
    404: {"description": "Group, browser, or image not found"},
})
async def remove_from_history(
    group_id: str = PathParam(...),
    browser_id: str = PathParam(...),
    image_path: str = PathParam(...),
    request: Request = None,
):
    """
    Remove a specific image from a browser's history.

    Called by the admin UI when a thumbnail fails to load, indicating
    the image file no longer exists. Removes from both image_history
    and forward_history arrays while preserving order of remaining items.
    """
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    browser = group.browsers.get(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser '{browser_id}' not found")

    endpoint = group.endpoints.get(browser.endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint not found for browser")

    # Decode the image path (it's URL-encoded in the path)
    decoded_path = unquote(image_path)

    # Remove from history arrays - list.remove() preserves order of remaining items
    removed = False
    if decoded_path in endpoint.image_history:
        endpoint.image_history.remove(decoded_path)
        removed = True
    if decoded_path in endpoint.forward_history:
        endpoint.forward_history.remove(decoded_path)
        removed = True

    if removed:
        # Persist cleaned history and notify admin clients for real-time UI update
        display_manager.save_config()
        await display_manager._notify_admins({
            "type": "GROUP_UPDATED",
            "group": group.to_dict()
        })
        logger.info(f"Removed stale image '{decoded_path}' from history for browser '{browser_id}'")

    return JSONResponse(content={"status": "removed" if removed else "not_found", "image_path": decoded_path})


@router.post("/api/display/groups/{group_id}/endpoints/{endpoint_id}/next")
async def endpoint_next_image(
    group_id: str = PathParam(...),
    endpoint_id: str = PathParam(...),
    request: Request = None,
    user: User = Depends(get_current_user)
):
    """
    Request next image for a specific endpoint.

    Useful for on-demand mode (auto_cycle=false) to manually advance images.
    Works even when no browser is connected - updates the endpoint's state.
    """
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    endpoint = group.endpoints.get(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    # Find connected browser for this endpoint
    browser = next(
        (b for b in group.browsers.values() if b.endpoint_id == endpoint_id),
        None
    )

    if browser:
        # Browser connected - use _advance_browsers
        await display_manager._advance_browsers(group, [browser.browser_id])
    else:
        # No browser - use helper method
        display_manager.advance_endpoint_no_browser(group, endpoint)

    logger.info(f"User {user.email} requested next image for endpoint '{endpoint_id}'")

    return JSONResponse(content={
        "status": "ok",
        "endpoint_id": endpoint_id,
        "current_image": endpoint.current_image
    })


@router.post("/api/display/groups/{group_id}/endpoints/{endpoint_id}/previous")
async def endpoint_previous_image(
    group_id: str = PathParam(...),
    endpoint_id: str = PathParam(...),
    request: Request = None,
    user: User = Depends(get_current_user)
):
    """
    Request previous image for a specific endpoint.

    Goes back in the endpoint's image history.
    """
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    endpoint = group.endpoints.get(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    # Find connected browser for this endpoint
    browser = next(
        (b for b in group.browsers.values() if b.endpoint_id == endpoint_id),
        None
    )

    if browser:
        # Browser connected - use _rewind_browsers
        await display_manager._rewind_browsers(group, [browser.browser_id])
    else:
        # No browser - use helper method
        display_manager.rewind_endpoint_no_browser(endpoint)

    logger.info(f"User {user.email} requested previous image for endpoint '{endpoint_id}'")

    return JSONResponse(content={
        "status": "ok",
        "endpoint_id": endpoint_id,
        "current_image": endpoint.current_image,
        "history_length": len(endpoint.image_history)
    })


@router.get("/display", include_in_schema=False)
async def display_page(
    request: Request,
    group: str = Query(..., description="Display group ID to join"),
    endpoint: Optional[str] = Query(
        None,
        description="Endpoint ID for persistent history",
        pattern=r"^[a-zA-Z0-9_\-]+$",
        max_length=64
    ),
    action: Optional[str] = Query(
        None,
        description="Action to perform: 'next' or 'previous'",
        pattern=r"^(next|previous)$"
    )
):
    """
    Serve the display page for a browser to join a display group.

    The browser connects via WebSocket to the specified display group
    and displays images synchronized with other browsers in the group.

    Query Parameters:
        group: The group ID to join (must exist, created via admin API)
        endpoint: Optional endpoint ID for persistent history across reconnects
        action: Optional action to perform before rendering ('next' or 'previous')
    """
    display_manager = get_display_manager(request)
    display_group = await display_manager.get_group(group)

    if not display_group:
        raise HTTPException(
            status_code=404,
            detail=f"Display group '{group}' not found. Create it via /admin first."
        )

    # Handle action parameter for on-demand navigation
    if action and endpoint:
        endpoint_obj = display_group.endpoints.get(endpoint)
        if not endpoint_obj:
            # Create endpoint if it doesn't exist
            endpoint_obj = display_group.get_or_create_endpoint(endpoint)

        # Find connected browser for this endpoint
        browser = next(
            (b for b in display_group.browsers.values() if b.endpoint_id == endpoint),
            None
        )

        if action == "next":
            if browser:
                await display_manager._advance_browsers(display_group, [browser.browser_id])
            else:
                display_manager.advance_endpoint_no_browser(display_group, endpoint_obj)
        elif action == "previous":
            if browser:
                await display_manager._rewind_browsers(display_group, [browser.browser_id])
            else:
                display_manager.rewind_endpoint_no_browser(endpoint_obj)

    # Get WebSocket URL - check X-Forwarded-Proto for reverse proxy support
    original_scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    ws_scheme = "wss" if original_scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{request.url.netloc}/ws/display/{group}"
    if endpoint:
        ws_url += f"?endpoint={endpoint}"

    return templates.TemplateResponse(
        "display.html",
        {
            "request": request,
            "group_id": group,
            "endpoint_id": endpoint,
            "ws_url": ws_url,
            "settings": display_group.settings.to_dict()
        }
    )


@router.websocket("/ws/admin")
async def websocket_admin(websocket: WebSocket):
    """
    WebSocket endpoint for display admin real-time updates.

    Protocol:
        Server -> Client:
        - INITIAL_STATE: { groups: [...] } - Sent on connect
        - GROUP_CREATED: { group: {...} } - New group
        - GROUP_DELETED: { group_id: "..." } - Group removed
        - GROUP_UPDATED: { group: {...} } - State changed (navigation, settings, browser join/leave)
        - PING: Keep-alive check

        Client -> Server:
        - PONG: Response to PING
    """
    display_manager = websocket.app.state.display_manager
    oidc_config = get_oidc_config()

    # Authentication check
    if oidc_config.enabled:
        # Check session for authenticated user
        user_info = websocket.session.get("user") if hasattr(websocket, 'session') else None
        if not user_info:
            await websocket.close(code=4001, reason="Not authenticated")
            return

    await websocket.accept()
    await display_manager.register_admin(websocket)
    logger.info("Admin WebSocket connected")

    try:
        # Send initial state
        groups = await display_manager.list_groups()
        await websocket.send_json({
            "type": "INITIAL_STATE",
            "groups": groups
        })

        # Keep connection alive, handle pings
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=30.0
                )
                if data.get("type") == "PONG":
                    pass  # Response to our ping, connection is alive
            except asyncio.TimeoutError:
                # Send ping to check connection
                try:
                    await websocket.send_json({"type": "PING"})
                except Exception:
                    break
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                pass  # Ignore malformed JSON
            except Exception as e:
                logger.debug(f"Admin WebSocket error: {e}")
                break

    finally:
        await display_manager.unregister_admin(websocket)
        logger.info("Admin WebSocket disconnected")


@router.websocket("/ws/display/{group_id}")
async def websocket_display(
    websocket: WebSocket,
    group_id: str,
    endpoint: Optional[str] = Query(
        None,
        description="Endpoint ID for persistent history",
        pattern=r"^[a-zA-Z0-9_\-]+$",
        max_length=64
    )
):
    """
    WebSocket endpoint for synchronized image display.

    Protocol:
        Server -> Client:
        - WELCOME: browser_id, endpoint_id, group_id, browser_count
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
    display_manager = websocket.app.state.display_manager
    browser = None  # Track for cleanup

    # Origin validation (S2) - only if ALLOWED_WS_ORIGINS is configured
    if ALLOWED_WS_ORIGINS:
        origin = websocket.headers.get("origin", "")
        if origin not in ALLOWED_WS_ORIGINS:
            logger.warning(f"WebSocket connection rejected: origin '{origin}' not in allowed list")
            await websocket.close(code=4003, reason="Forbidden origin")
            return

    # Set base URL from first request
    if not display_manager._base_url:
        base_url = os.environ.get('BASE_URL', '')
        if not base_url:
            scheme = "https" if websocket.url.scheme == "wss" else "http"
            host = websocket.headers.get("host", "localhost")
            base_url = f"{scheme}://{host}"
        display_manager.set_base_url(base_url)

    # Check if group exists BEFORE accepting (#4)
    group = await display_manager.get_group(group_id)
    if not group:
        # Close without accepting - client receives close frame
        await websocket.close(code=4004, reason=f"Group '{group_id}' not found")
        return

    # Accept the connection
    await websocket.accept()

    try:
        # Add browser to group (may fail if group deleted or limit reached)
        try:
            browser = await display_manager.add_browser(group_id, websocket, endpoint)
        except ValueError as e:
            # Connection limit reached (A10)
            await websocket.close(code=4008, reason=str(e))
            return

        if not browser:
            await websocket.close(code=4004, reason="Failed to join group")
            return

        # Refresh group reference after adding browser
        group = await display_manager.get_group(group_id)
        if not group:
            await websocket.close(code=4004, reason="Group no longer exists")
            return

        # Get the endpoint for this browser
        display_endpoint = group.get_endpoint_for_browser(browser.browser_id)

        # Send welcome message
        await websocket.send_json({
            "type": "WELCOME",
            "browser_id": browser.browser_id,
            "endpoint_id": browser.endpoint_id,
            "group_id": group_id,
            "browser_count": len(group.browsers),
            "settings": group.settings.to_dict()
        })

        # Send initial image assignment (from endpoint's current_image)
        current_image = display_endpoint.current_image if display_endpoint else None
        if current_image:
            image_url = display_manager.build_image_url(
                current_image,
                group.settings
            )
            blur_url = display_manager.build_blur_url(
                current_image,
                group.settings
            )
            await websocket.send_json({
                "type": "ASSIGN",
                "image_url": image_url,
                "blur_url": blur_url,
                "image_id": current_image,
                "cycle": group.current_cycle
            })
        else:
            # No image available - send error to browser
            error_msg = group.image_pool.error or "No images configured for this group"
            await websocket.send_json({
                "type": "NO_IMAGES",
                "error": error_msg
            })

        # Handle incoming messages
        while True:
            try:
                data = await websocket.receive_json()
                await display_manager.handle_browser_message(
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
                await display_manager.remove_browser(group_id, browser.browser_id)
                logger.info(f"Browser {browser.browser_id} disconnected from {group_id}")
            except Exception as e:
                logger.error(f"Error during browser cleanup: {e}")


# =============================================================================
# Immich Provider API - Endpoints
# =============================================================================

def get_immich_provider(request: Request) -> ImmichProvider:
    """Get the ImmichProvider from app state"""
    return request.app.state.immich_provider


class ImmichTestConnectionRequest(BaseModel):
    """Request body for testing Immich connection"""
    server_url: str = Field(..., description="Immich server URL")
    api_key: Optional[str] = Field(None, description="API key for authentication")
    pull_name: Optional[str] = Field(None, description="Pull name to use stored API key")


class ImmichPullCreateRequest(BaseModel):
    """Request body for creating/updating an Immich pull configuration"""
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    server_url: str = Field(..., description="Immich server URL")
    api_key: Optional[str] = Field(None, description="API key (omit to keep existing when updating)")
    target_folder: str = Field(..., description="Local folder to store images")
    count: int = Field(50, ge=1, le=1000, description="Number of images to pull")
    album_id: Optional[str] = Field(None, description="Pull from specific album")
    person_ids: Optional[list[str]] = Field(None, description="Filter by people")
    is_favorite: Optional[bool] = Field(None, description="Only favorites")
    taken_after: Optional[str] = Field(None, description="ISO date (YYYY-MM-DD)")
    taken_before: Optional[str] = Field(None, description="ISO date (YYYY-MM-DD)")
    media_type: str = Field("IMAGE", description="IMAGE or VIDEO")
    replace_existing: bool = Field(True, description="Clear folder before pulling")


@router.get(
    "/api/immich/pulls",
    responses={
        200: {"description": "List of configured Immich pulls"},
        401: {"description": "Authentication required"},
    }
)
async def list_immich_pulls(
    request: Request,
    user: User = Depends(get_current_user)
):
    """
    List all configured Immich pull operations.

    Returns the list of pull configurations (API keys are masked).
    """
    provider = get_immich_provider(request)
    pulls = provider.list_pulls()

    # Mask API keys for security
    for pull in pulls:
        if pull.get("api_key"):
            pull["api_key"] = "***" + pull["api_key"][-4:] if len(pull["api_key"]) > 4 else "***"

    return JSONResponse(content={"pulls": pulls})


@router.post(
    "/api/immich/pulls",
    responses={
        200: {"description": "Pull configuration created/updated"},
        401: {"description": "Authentication required"},
    }
)
async def create_immich_pull(
    request: Request,
    body: ImmichPullCreateRequest,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Create or update an Immich pull configuration.

    Requires authentication and CSRF token.
    """
    provider = get_immich_provider(request)
    logger.info(f"User {user.email} creating/updating Immich pull: {body.name}")

    # Get existing config to preserve API key if not provided
    api_key = body.api_key
    if not api_key:
        existing = provider.get_pull(body.name)
        if existing:
            api_key = existing.api_key
        else:
            raise HTTPException(status_code=400, detail="API key is required for new pulls")

    config = ImmichPullConfig(
        name=body.name,
        server_url=body.server_url,
        api_key=api_key,
        target_folder=body.target_folder,
        count=body.count,
        album_id=body.album_id,
        person_ids=body.person_ids,
        is_favorite=body.is_favorite,
        taken_after=body.taken_after,
        taken_before=body.taken_before,
        media_type=body.media_type,
        replace_existing=body.replace_existing,
    )

    provider.add_pull(config)
    return JSONResponse(content={"message": f"Pull '{body.name}' configured"})


@router.delete(
    "/api/immich/pulls/{pull_name}",
    responses={
        200: {"description": "Pull configuration deleted"},
        401: {"description": "Authentication required"},
        404: {"description": "Pull not found"},
    }
)
async def delete_immich_pull(
    request: Request,
    pull_name: str,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Delete an Immich pull configuration.

    Requires authentication and CSRF token.
    """
    provider = get_immich_provider(request)
    logger.info(f"User {user.email} deleting Immich pull: {pull_name}")

    if provider.remove_pull(pull_name):
        return JSONResponse(content={"message": f"Pull '{pull_name}' deleted"})
    else:
        raise HTTPException(status_code=404, detail=f"Pull '{pull_name}' not found")


@router.post(
    "/api/immich/pulls/save",
    responses={
        200: {"description": "Configuration saved"},
        401: {"description": "Authentication required"},
    }
)
async def save_immich_config(
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Save current Immich pull configurations to file.

    Requires authentication and CSRF token.
    """
    provider = get_immich_provider(request)
    logger.info(f"User {user.email} saving Immich config")
    saved = provider.save_config()
    return JSONResponse(content={"saved": saved, "message": f"Saved {saved} pull configurations"})


@router.post(
    "/api/immich/pull/{pull_name}",
    responses={
        200: {"description": "Pull operation started"},
        401: {"description": "Authentication required"},
        404: {"description": "Pull not found"},
    }
)
async def trigger_immich_pull(
    request: Request,
    pull_name: str,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Trigger an Immich pull operation.

    Starts the pull in the background and returns immediately.
    Use GET /api/immich/pull/{pull_name}/status to check progress.
    """
    provider = get_immich_provider(request)

    if not provider.get_pull(pull_name):
        raise HTTPException(status_code=404, detail=f"Pull '{pull_name}' not found")

    logger.info(f"User {user.email} triggering Immich pull: {pull_name}")
    result = await provider.start_pull(pull_name)

    return JSONResponse(content={
        "status": "started",
        "pull_name": pull_name,
        "result": result.to_dict(),
    })


@router.get(
    "/api/immich/pull/{pull_name}/status",
    responses={
        200: {"description": "Pull operation status"},
        401: {"description": "Authentication required"},
        404: {"description": "Pull not found"},
    }
)
async def get_immich_pull_status(
    request: Request,
    pull_name: str,
    user: User = Depends(get_current_user)
):
    """
    Get the status of an Immich pull operation.

    Returns current progress if running, or final result if completed.
    """
    provider = get_immich_provider(request)

    if not provider.get_pull(pull_name):
        raise HTTPException(status_code=404, detail=f"Pull '{pull_name}' not found")

    result = provider.get_pull_status(pull_name)
    if result:
        return JSONResponse(content=result.to_dict())
    else:
        return JSONResponse(content={"status": "idle", "pull_name": pull_name})


@router.post(
    "/api/immich/test-connection",
    responses={
        200: {"description": "Connection test result"},
        401: {"description": "Authentication required"},
    }
)
async def test_immich_connection(
    request: Request,
    body: ImmichTestConnectionRequest,
    user: User = Depends(get_current_user)
):
    """
    Test connection to an Immich server.

    Returns server info if successful, error message if not.
    If api_key is not provided, looks up stored key from pull_name.
    """
    provider = get_immich_provider(request)

    # Get API key - use provided key or look up from stored pull
    api_key = body.api_key
    if not api_key and body.pull_name:
        existing = provider.get_pull(body.pull_name)
        if existing:
            api_key = existing.api_key

    if not api_key:
        return JSONResponse(content={"success": False, "error": "API key required"})

    logger.info(f"User {user.email} testing Immich connection to: {body.server_url}")
    result = await provider.test_connection(body.server_url, api_key)
    return JSONResponse(content=result)


@router.get(
    "/api/immich/albums",
    responses={
        200: {"description": "List of albums from Immich"},
        401: {"description": "Authentication required"},
        500: {"description": "Failed to fetch albums"},
    }
)
async def list_immich_albums(
    request: Request,
    server_url: str = Query(..., description="Immich server URL"),
    api_key: Optional[str] = Query(None, description="API key"),
    pull_name: Optional[str] = Query(None, description="Pull name to use stored API key"),
    user: User = Depends(get_current_user)
):
    """
    List available albums from an Immich server.

    Useful for configuring album-based pulls.
    If api_key is not provided, looks up stored key from pull_name.
    """
    provider = get_immich_provider(request)

    # Get API key - use provided key or look up from stored pull
    actual_key = api_key
    if not actual_key and pull_name:
        existing = provider.get_pull(pull_name)
        if existing:
            actual_key = existing.api_key

    if not actual_key:
        raise HTTPException(status_code=400, detail="API key required")

    try:
        albums = await provider.list_albums(server_url, actual_key)
        return JSONResponse(content={"albums": albums})
    except Exception as e:
        logger.error(f"Failed to fetch Immich albums: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/api/immich/people",
    responses={
        200: {"description": "List of recognized people from Immich"},
        401: {"description": "Authentication required"},
        500: {"description": "Failed to fetch people"},
    }
)
async def list_immich_people(
    request: Request,
    server_url: str = Query(..., description="Immich server URL"),
    api_key: Optional[str] = Query(None, description="API key"),
    pull_name: Optional[str] = Query(None, description="Pull name to use stored API key"),
    user: User = Depends(get_current_user)
):
    """
    List recognized people from an Immich server.

    Useful for configuring person-based pulls.
    If api_key is not provided, looks up stored key from pull_name.
    """
    provider = get_immich_provider(request)

    # Get API key - use provided key or look up from stored pull
    actual_key = api_key
    if not actual_key and pull_name:
        existing = provider.get_pull(pull_name)
        if existing:
            actual_key = existing.api_key

    if not actual_key:
        raise HTTPException(status_code=400, detail="API key required")

    try:
        people = await provider.list_people(server_url, actual_key)
        return JSONResponse(content={"people": people})
    except Exception as e:
        logger.error(f"Failed to fetch Immich people: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CreateFolderRequest(BaseModel):
    """Request model for creating a folder."""
    path: str = Field(..., description="Folder path relative to image directory")


@router.post(
    "/api/immich/create-folder",
    responses={
        200: {"description": "Folder created successfully"},
        400: {"description": "Invalid folder path"},
        401: {"description": "Authentication required"},
        500: {"description": "Failed to create folder"},
    }
)
async def create_folder(
    request: Request,
    body: CreateFolderRequest,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Create a new folder in the image directory.

    Used by the folder browser when setting up Immich pull target folders.
    """
    folder_path = body.path.strip()

    # Validate folder path
    if not folder_path:
        raise HTTPException(status_code=400, detail="Folder path is required")

    if ".." in folder_path or folder_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid folder path")

    # Get base image directory
    base_dir = Path(os.environ.get("IMAGE_DIR", "/www/images"))

    try:
        # Resolve and validate the path
        full_path = (base_dir / folder_path).resolve()

        # Ensure the path is within the base directory (security check)
        if not str(full_path).startswith(str(base_dir.resolve())):
            raise HTTPException(status_code=400, detail="Invalid folder path")

        # Create the folder
        full_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created folder: {folder_path}")

        return JSONResponse(content={"success": True, "path": folder_path})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create folder {folder_path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
