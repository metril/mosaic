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
from typing import Optional, Literal
from urllib.parse import unquote
from fastapi import APIRouter, Query, HTTPException, Path as PathParam
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.services.image_service import ImageService
from app.exceptions import PathSecurityError, NoImagesFoundError


router = APIRouter()
image_service = ImageService()
templates = Jinja2Templates(directory="templates")

@router.get("/favicon.ico")
async def favicon():
    """Serve favicon.ico file."""
    return FileResponse(
        "app/static/favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"}
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


@router.get("/browse")
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


@router.get("/thumbnail/{image_path:path}")
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

        # Validate the path using security checks
        from app.utils.path_security import validate_folder_path

        # Split into folder and filename
        path_parts = Path(image_path).parts
        if len(path_parts) > 1:
            # Has folder component
            folder_path = str(Path(*path_parts[:-1]))
            filename = path_parts[-1]
            validated_folder = validate_folder_path(folder_path, image_service.image_dir)
            full_path = validated_folder / filename
        else:
            # Just filename in root
            filename = path_parts[0]
            full_path = image_service.image_dir / filename

        # Security check: ensure file exists and is not a symlink
        if not full_path.exists() or full_path.is_symlink():
            return JSONResponse(
                status_code=404,
                content={"error": "Image not found"}
            )

        # Security check: ensure it's actually within image_dir
        try:
            full_path.relative_to(image_service.image_dir)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid image path"}
            )

        # Load and resize image in thread pool
        def process_thumbnail():
            image = image_service.load_image(full_path)
            # Generate 200x200 thumbnail with crop mode
            thumbnail = image_service.resize_image(
                image,
                width=200,
                height=200,
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

        # Get buffer size
        buffer.seek(0, 2)
        content_length = buffer.tell()
        buffer.seek(0)

        return StreamingResponse(
            buffer,
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
            content={"error": "Invalid image path"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to generate thumbnail"}
        )


@router.get("/metadata/{image_path:path}")
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

        # Validate the path using security checks
        from app.utils.path_security import validate_folder_path

        # Split into folder and filename
        path_parts = Path(image_path).parts
        if len(path_parts) > 1:
            folder_path = str(Path(*path_parts[:-1]))
            filename = path_parts[-1]
            validated_folder = validate_folder_path(folder_path, image_service.image_dir)
            full_path = validated_folder / filename
        else:
            filename = path_parts[0]
            full_path = image_service.image_dir / filename

        # Check if file exists
        if not full_path.exists() or not full_path.is_file():
            return JSONResponse(
                status_code=404,
                content={"error": "Image not found"}
            )

        # Get metadata (runs in thread pool for EXIF extraction)
        def extract_metadata():
            return image_service.get_image_metadata(full_path, include_exif=include_exif)

        metadata = await asyncio.get_event_loop().run_in_executor(None, extract_metadata)

        return JSONResponse(content=metadata)

    except PathSecurityError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid image path"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to extract metadata: {str(e)}"}
        )


@router.get("/api/image/{image_path:path}")
async def serve_processed_image(
    image_path: str,
    width: Optional[int] = Query(None, description="Target width in pixels", ge=1),
    height: Optional[int] = Query(None, description="Target height in pixels", ge=1),
    preserve_aspect: bool = Query(True, description="Preserve aspect ratio"),
    mode: Literal['fit', 'crop'] = Query('fit', description="Resize mode"),
    eink: bool = Query(False, description="Apply E-Ink color mapping"),
    format: Optional[Literal['jpeg', 'png', 'webp', 'bmp', 'gif']] = Query(None),
    saturation: float = Query(2.5, description="E-Ink saturation", ge=0.0, le=10.0),
    contrast: float = Query(1.2, description="E-Ink contrast", ge=0.0, le=5.0),
    brightness: float = Query(1.1, description="E-Ink brightness", ge=0.0, le=5.0),
    sharpen: bool = Query(True, description="Apply sharpening"),
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

        # Validate path security
        from app.utils.path_security import validate_folder_path

        path_parts = Path(image_path).parts
        if len(path_parts) > 1:
            folder_path = str(Path(*path_parts[:-1]))
            filename = path_parts[-1]
            validated_folder = validate_folder_path(folder_path, image_service.image_dir)
            full_path = validated_folder / filename
        else:
            filename = path_parts[0]
            full_path = image_service.image_dir / filename

        # Check file exists
        if not full_path.exists() or not full_path.is_file():
            return JSONResponse(
                status_code=404,
                content={"error": "Image not found"}
            )

        # Process image in thread pool (CPU-intensive operations)
        def process():
            from app.utils.color_mapping import apply_spectra_6

            img = image_service.load_image(full_path)

            # Apply resizing if requested
            if width or height:
                img = image_service.resize_image(
                    img, width, height, preserve_aspect, mode
                )

            # Apply E-Ink processing if requested
            # apply_spectra_6 handles all preprocessing: sharpen, saturation, contrast, brightness, palette mapping
            if eink:
                img = apply_spectra_6(img, saturation, contrast, brightness, sharpen)

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
            buffer.seek(0)

            content_type = f"image/{output_format}"
            filename_out = f"{full_path.stem}.{output_format}"

            # Get final dimensions and file mtime
            img_width, img_height = img.size
            mtime = full_path.stat().st_mtime

            return buffer, content_type, filename_out, img_width, img_height, mtime

        buffer, content_type, filename_out, img_width, img_height, mtime = await asyncio.get_event_loop().run_in_executor(
            None, process
        )

        # Get content length
        buffer.seek(0, 2)
        content_length = buffer.tell()
        buffer.seek(0)

        # Generate ETag from content hash
        etag = hashlib.md5(buffer.read()).hexdigest()
        buffer.seek(0)

        # Build Content-Location URI for this specific image
        # RFC 7231: indicates a URI that can be used as an identifier for this resource
        content_location = f"/api/image/{image_path}"

        # Format Last-Modified as RFC 7231 HTTP-date
        last_modified = formatdate(mtime, usegmt=True)

        return StreamingResponse(
            buffer,
            media_type=content_type,
            headers={
                "Content-Length": str(content_length),
                # Disable caching for processed images to allow parameter changes
                "Cache-Control": "no-cache, no-store, must-revalidate" if eink else "public, max-age=86400",
                "Content-Disposition": f'inline; filename="{filename_out}"',
                "Content-Location": content_location,
                "X-Image-Width": str(img_width),
                "X-Image-Height": str(img_height),
                "ETag": f'"{etag}"',
                "Last-Modified": last_modified,
            }
        )

    except PathSecurityError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid image path"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to process image: {str(e)}"}
        )


@router.get("/{folder_path:path}")
async def root(
    request: Request,
    folder_path: str,
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

        buffer, content_type, filename, relative_path, img_width, img_height, mtime = result

        # Get buffer size for Content-Length header
        buffer.seek(0, 2)  # Seek to end
        content_length = buffer.tell()
        buffer.seek(0)  # Seek back to beginning

        # Generate ETag from content hash
        etag = hashlib.md5(buffer.read()).hexdigest()
        buffer.seek(0)

        # Build Content-Location URI for re-requesting this specific image
        # RFC 7231: indicates a URI that can be used as an identifier for this resource
        content_location = f"/api/image/{relative_path}"

        # Format Last-Modified as RFC 7231 HTTP-date
        last_modified = formatdate(mtime, usegmt=True)

        return StreamingResponse(
            buffer,
            media_type=content_type,
            headers={
                "Content-Length": str(content_length),
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Location": content_location,
                "X-Image-Width": str(img_width),
                "X-Image-Height": str(img_height),
                "ETag": f'"{etag}"',
                "Last-Modified": last_modified,
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
