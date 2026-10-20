"""
API Routes for Mosaic

Provides endpoints for random image serving and web interface.
"""

import os
import asyncio
import json
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
from io import BytesIO
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Literal, Dict, Any, List
from urllib.parse import quote, unquote
from fastapi import APIRouter, Query, HTTPException, Path as PathParam, WebSocket, WebSocketDisconnect, Depends, Body
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
from app.auth.dependencies import get_current_user, get_optional_user, get_admin_auth, User, get_csrf_token, verify_csrf_token
from app.auth.oidc import get_oidc_config
from app.auth.api_keys import APIKeyManager, APIKey
from app.exceptions import PathSecurityError, NoImagesFoundError, error_response
from app.utils.path_security import resolve_image_path
from app.utils.logging import logger, log_buffer
from app.utils.metrics import metrics
from app.utils.blur import create_blur_background, should_skip_blur
from app.utils.lru_cache import BoundedLRU
from app.utils.rate_limit import (
    limiter,
    IMAGE_RATE_LIMIT,
    EINK_COST,
    THUMBNAIL_RATE_LIMIT,
    BLUR_RATE_LIMIT,
)
from app.services.thumbnail_cache import ThumbnailCache
from app.services import cron_utils
from app.services.immich_stream import StreamNotFound, StreamUpstreamError

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
STATIC_CACHE_MAX_AGE = int(os.environ.get('STATIC_CACHE_MAX_AGE', '86400'))
IMAGE_JPEG_QUALITY = int(os.environ.get('IMAGE_JPEG_QUALITY', '95'))
MAX_IMMICH_PULL_COUNT = int(os.environ.get('MAX_IMMICH_PULL_COUNT', '1000'))


router = APIRouter()
image_service = ImageService()
templates = Jinja2Templates(directory="templates")

# -----------------------------------------------------------------------------
# SPA (React) shell serving
#
# The frontend in web/ builds to app/static/spa/. When that build is present,
# routes listed in SPA_ROUTES return the SPA shell (index.html) so React Router
# takes over client-side; every other page keeps its existing Jinja template.
# This lets the SPA migration roll out one page at a time without breaking the
# pages that haven't been migrated yet, and without touching the legacy
# random-image / E-Ink endpoints.
#
# SPA_ROUTES grows per phase (Phase 1 adds "/" and "/browse", Phase 2 "/admin",
# Phase 3 "/display"). It can be overridden at deploy time via the SPA_ROUTES
# env var (comma-separated) for testing/rollback.
# -----------------------------------------------------------------------------
_SPA_INDEX = Path(__file__).parent.parent / "static" / "spa" / "index.html"
# Migrated routes, grown per phase: Home, Browse, Admin, and the Display kiosk.
# The whole dashboard is now the SPA. Override at deploy time with SPA_ROUTES.
_DEFAULT_SPA_ROUTES = "/,/browse,/admin,/display"
SPA_ROUTES = {
    r.strip()
    for r in os.environ.get("SPA_ROUTES", _DEFAULT_SPA_ROUTES).split(",")
    if r.strip()
}


def spa_shell(route: str) -> Optional[FileResponse]:
    """Return the built SPA shell for `route` if the SPA is built and that route
    has been migrated, otherwise None.

    The shell itself is never cached (so deploys are picked up immediately); the
    hashed /assets/* bundles it references are safe to cache long-term.
    """
    if route in SPA_ROUTES and _SPA_INDEX.exists():
        return FileResponse(_SPA_INDEX, headers={"Cache-Control": "no-cache"})
    return None


def spa_shell_or_503(route: str) -> FileResponse:
    """The SPA is the only UI (the legacy Jinja pages were removed) — fail
    loudly with a clear message if the frontend build is missing rather than
    rendering a broken page."""
    resp = spa_shell(route)
    if resp is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Frontend build not found. Build the SPA (cd web && npm run build) "
                "or use the Docker image, which builds it automatically."
            ),
        )
    return resp


@router.get("/api/csrf", include_in_schema=False)
async def get_csrf(request: Request):
    """Return the session CSRF token for the SPA.

    The Jinja pages inject this token directly into the template; the SPA has no
    such injection point, so it fetches the same session-bound token here and
    sends it back as the X-CSRF-Token header on mutating requests. Reuses the
    existing session-based token logic (get_csrf_token). Same-origin policy +
    the session cookie prevent a cross-origin page from reading this response.
    """
    return JSONResponse(content={"csrf_token": get_csrf_token(request)})

# Bounded thread pool for CPU-bound image work (thumbnails, resizing, blur,
# metadata). Using the default executor would let thread count grow unbounded
# under load; cap it to keep memory/thread usage predictable.
_IMAGE_WORKERS = int(os.environ.get("IMAGE_WORKER_THREADS", str(min(32, (os.cpu_count() or 4) * 2))))
image_executor = ThreadPoolExecutor(max_workers=_IMAGE_WORKERS, thread_name_prefix="img")

# Server-side cache of fully-processed image bytes for /api/image. The output is
# deterministic in (path, mtime, processing params), so repeat requests — and
# even conditional (304) requests, which otherwise re-encode just to recompute
# the ETag — are served without touching the executor. Keyed with mtime so a
# changed source file produces a fresh key; bounded by a byte budget. The E-Ink
# path benefits most (it sends Cache-Control: no-cache, so clients always
# re-request). Set PROCESSED_IMAGE_CACHE_BYTES=0 to disable.
PROCESSED_IMAGE_CACHE_BYTES = int(os.environ.get("PROCESSED_IMAGE_CACHE_BYTES", str(256 * 1024 * 1024)))
processed_image_cache = BoundedLRU(PROCESSED_IMAGE_CACHE_BYTES)


def _processed_cache_key(image_path: str, mtime: float, *params: Any) -> str:
    return image_path + "\x00" + repr(mtime) + "\x00" + "\x00".join(repr(p) for p in params)


# Per-(path, mtime) memo of whether a source file carries an EXIF orientation
# that requires transposing pixels. Lets /api/image stream original bytes for
# the (overwhelmingly common) already-upright case without opening the file on
# every request.
_orientation_cache: "OrderedDict[tuple, bool]" = OrderedDict()
_ORIENTATION_CACHE_MAX = 8192


def _needs_exif_transpose(path: Path, mtime: float) -> bool:
    key = (str(path), mtime)
    cached = _orientation_cache.get(key)
    if cached is not None:
        _orientation_cache.move_to_end(key)
        return cached
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(path) as img:
            orientation = img.getexif().get(0x0112, 1)
        needs = orientation not in (None, 0, 1)
    except Exception:
        # If we can't read the header, fall back to the full processing path.
        needs = True
    _orientation_cache[key] = needs
    while len(_orientation_cache) > _ORIENTATION_CACHE_MAX:
        _orientation_cache.popitem(last=False)
    return needs


def _etag_matches(if_none_match: Optional[str], etag: str) -> bool:
    """Per-RFC-7232 If-None-Match comparison for a strong ETag value.

    Handles ``*``, comma-separated lists, and weak (``W/"..."``) validators,
    stripping exactly one surrounding quote pair rather than all quotes.
    """
    if not if_none_match:
        return False
    for candidate in if_none_match.split(","):
        token = candidate.strip()
        if token == "*":
            return True
        if token.startswith("W/"):
            token = token[2:].strip()
        if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
            token = token[1:-1]
        if token == etag:
            return True
    return False


from typing import Union
from fastapi import Header


def _has_valid_scopes(scopes: Optional[List[str]]) -> bool:
    """Check if API key has valid (non-legacy, non-empty) scopes."""
    if not scopes:
        return False
    # Legacy scope = no access (treated as unconfigured)
    if scopes == ["images:read"]:
        return False
    return True


async def get_image_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> Optional[Union[APIKey, User]]:
    """
    Check for API key in Authorization header or user session.

    Used for image serving endpoints that require auth when OIDC is enabled.

    Returns:
        APIKey if valid API key provided
        User if authenticated via session
        None if no auth (caller must check OIDC config)

    Raises:
        HTTPException 401 if invalid API key provided
    """
    # Check API key first (Authorization: Bearer sk_xxx)
    if authorization and authorization.startswith("Bearer "):
        key = authorization[7:]
        if hasattr(request.app.state, 'api_key_manager'):
            # Argon2id verification is CPU-heavy (~tens of ms); run it off the
            # event loop so concurrent requests aren't serialized behind it.
            api_key = await asyncio.get_running_loop().run_in_executor(
                None, request.app.state.api_key_manager.verify_key, key
            )
            if api_key:
                if not _has_valid_scopes(api_key.scopes):
                    raise HTTPException(status_code=403, detail="API key has no access configured")
                return api_key
        # Invalid key provided = reject immediately
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Check for API key in query parameter (for display endpoints)
    key_param = request.query_params.get("key")
    if key_param and key_param.startswith("sk_"):
        if hasattr(request.app.state, 'api_key_manager'):
            api_key = await asyncio.get_running_loop().run_in_executor(
                None, request.app.state.api_key_manager.verify_key, key_param
            )
            if api_key:
                if not _has_valid_scopes(api_key.scopes):
                    raise HTTPException(status_code=403, detail="API key has no access configured")
                # Store in session for subsequent requests (e.g., WebSocket)
                request.session["api_key_id"] = api_key.id
                return api_key
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Check session for previously validated API key
    if "api_key_id" in request.session:
        if hasattr(request.app.state, 'api_key_manager'):
            api_key = request.app.state.api_key_manager.get_key(request.session["api_key_id"])
            # Only return API key if enabled AND has valid scopes
            if api_key and api_key.enabled and _has_valid_scopes(api_key.scopes):
                return api_key

    # Fall back to session auth
    config = get_oidc_config()
    if config.enabled:
        user_info = request.session.get("user")
        if user_info:
            return User(user_info)
        return None  # No auth

    # OIDC disabled = allow with dev user
    return User({"sub": "dev", "email": "dev@localhost", "name": "Developer"})


def check_scope_access(
    api_key: APIKey,
    request: Request,
    group_id: Optional[str] = None,
    endpoint_id: Optional[str] = None,
    image_path: Optional[str] = None,
) -> bool:
    """
    Check if API key scopes grant access to the requested resource.

    Scope patterns:
    - "*" - Full access to everything
    - "group:{group_id}" - All endpoints + folders for a group
    - "endpoint:{group_id}:{endpoint_id}" - Specific endpoint only
    - "folder:{folder_name}" - Direct folder access

    Args:
        api_key: The validated API key
        request: FastAPI request (for accessing display_manager)
        group_id: For display endpoints - the group being accessed
        endpoint_id: For display endpoints - the endpoint being accessed
        image_path: For image endpoints - the path being accessed

    Returns:
        True if access is allowed, False otherwise
    """
    scopes = api_key.scopes

    # Empty or legacy scopes = no access
    if not _has_valid_scopes(scopes):
        return False

    # Full access
    if "*" in scopes:
        return True

    # Check display endpoint access
    if group_id:
        # Endpoint-level scope - explicit access to specific endpoint
        if endpoint_id and f"endpoint:{group_id}:{endpoint_id}" in scopes:
            return True

        # Group-level scope - allows anonymous endpoints only (not persistent)
        if f"group:{group_id}" in scopes:
            # If no endpoint specified, allow (will use anonymous)
            if not endpoint_id:
                return True
            # If endpoint is anonymous (starts with anon-), allow
            if endpoint_id.startswith("anon-"):
                return True
            # Check if endpoint is persistent - if so, deny group-level access
            display_manager = getattr(request.app.state, 'display_manager', None)
            if display_manager:
                group = display_manager.groups.get(group_id)
                if group:
                    endpoint = group.endpoints.get(endpoint_id)
                    # If endpoint exists and is persistent, require explicit scope
                    if endpoint and endpoint.is_persistent:
                        return False
            # Endpoint doesn't exist yet or is not persistent - allow
            return True

    # Check image/folder access
    if image_path:
        path_parts = image_path.strip("/").split("/")
        if not path_parts:
            return False

        folder = path_parts[0]

        # Wildcard folder scope - access all folders
        if "folder:*" in scopes:
            return True

        # Direct folder scope
        if f"folder:{folder}" in scopes:
            return True

        # Folder via group membership
        display_manager = getattr(request.app.state, 'display_manager', None)
        if display_manager:
            for scope in scopes:
                if scope.startswith("group:"):
                    grp_id = scope.split(":", 1)[1]
                    group = display_manager.groups.get(grp_id)
                    if group and group.settings.folders:
                        group_folders = [f.strip() for f in group.settings.folders.split(",")]
                        if folder in group_folders:
                            return True

    return False


def check_admin_scope(auth, required_scope: str) -> None:
    """
    Verify API key has the required admin scope. No-op for session users.

    Supports hierarchy:
    - "*" grants everything
    - "admin:*" grants all admin scopes
    - "admin:display:*" grants all display scopes
    - "admin:display:monitor" grants specific scope
    """
    if not isinstance(auth, APIKey):
        return  # Session users have full admin access

    scopes = auth.scopes
    if "*" in scopes or "admin:*" in scopes:
        return

    # Check category wildcard (e.g., "admin:display:*" covers "admin:display:monitor")
    parts = required_scope.split(":")
    if len(parts) == 3:
        category_wildcard = f"{parts[0]}:{parts[1]}:*"
        if category_wildcard in scopes:
            return

    if required_scope in scopes:
        return

    raise HTTPException(
        status_code=403,
        detail=f"API key lacks required scope: {required_scope}"
    )


def _ascii_filename(name: str) -> str:
    """Sanitize filename for Content-Disposition header (must be Latin-1 encodable)."""
    return name.encode('ascii', 'replace').decode('ascii').replace('?', '_')


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
        headers={"Cache-Control": f"public, max-age={STATIC_CACHE_MAX_AGE}"}
    )


# Cached image count for /health — the endpoint is polled frequently (Docker
# healthcheck every 30s, uptime monitors) and a full directory walk on each
# call would block the event loop on large libraries.
_image_count_cache: Dict[str, Any] = {"count": 0, "ts": 0.0, "primed": False}

# Cached fast browse index (folders-only tree + flat stat-only file list) shared
# by /api/browse/folders and /api/browse/files. Built once via the stat-only
# scan and reused until the file watcher invalidates it (on_folder_changed).
_browse_index_lock = asyncio.Lock()
_browse_index_cache: Dict[str, Any] = {"data": None}


def invalidate_browse_cache() -> None:
    """Drop the cached browse index so the next request rebuilds it."""
    _browse_index_cache["data"] = None


async def _get_browse_index() -> Dict[str, Any]:
    """Return the cached fast browse index, building it off-thread on a miss."""
    data = _browse_index_cache["data"]
    if data is not None:
        return data
    async with _browse_index_lock:
        # Re-check: another coroutine may have built it while we awaited the lock.
        if _browse_index_cache["data"] is None:
            _browse_index_cache["data"] = await asyncio.get_running_loop().run_in_executor(
                image_executor, image_service.build_browse_index
            )
        return _browse_index_cache["data"]
_IMAGE_COUNT_TTL = 60.0  # seconds


async def _get_cached_image_count() -> int:
    """Return the image count, refreshing off-thread at most every TTL seconds."""
    import time
    now = time.monotonic()
    if not _image_count_cache["primed"] or (now - _image_count_cache["ts"]) > _IMAGE_COUNT_TTL:
        count = await asyncio.get_running_loop().run_in_executor(
            image_executor, lambda: len(image_service.get_image_files())
        )
        _image_count_cache["count"] = count
        _image_count_cache["ts"] = now
        _image_count_cache["primed"] = True
    return _image_count_cache["count"]


@router.get("/health", responses={
    200: {
        "description": "Health status with image count",
        "content": {
            "application/json": {
                "example": {"status": "healthy", "image_count": 42}
            }
        }
    }
})
async def health_check(request: Request):
    """
    Health check endpoint (unauthenticated — keep the payload free of
    filesystem details).

    Returns:
        JSON with status, image count and Immich stream-cache stats
    """
    image_count = await _get_cached_image_count()
    fetcher = getattr(request.app.state, "stream_fetcher", None)

    return {
        "status": "healthy",
        "image_count": image_count,
        "stream_cache": fetcher.stats() if fetcher else {
            "entries": 0, "bytes": 0, "max_bytes": 0, "hits": 0, "misses": 0,
        },
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


@router.get("/api/homeassistant/entities", responses={
    200: {"description": "HA entities in a domain (for picker UIs)"},
    503: {"description": "Home Assistant not available"},
})
async def list_homeassistant_entities(
    request: Request,
    domain: str = Query("weather", description="Entity domain to list, e.g. weather, sun"),
    user: User = Depends(get_current_user),
):
    """List Home Assistant entities of a domain, for the settings/weather pickers."""
    ha_provider = getattr(request.app.state, 'ha_provider', None)
    if not ha_provider:
        return JSONResponse(status_code=503, content={"entities": [], "error": "Home Assistant not configured"})
    try:
        entities = await ha_provider.get_entities(domain)
    except Exception as e:
        return JSONResponse(status_code=503, content={"entities": [], "error": str(e)})
    return {"entities": entities}


@router.get("/api/settings", responses={
    200: {"description": "Runtime-tunable settings (secrets masked)"},
    401: {"description": "Authentication required"},
})
async def get_settings(request: Request, user: User = Depends(get_current_user)):
    """Return the managed runtime settings; secrets are masked to has_* booleans."""
    return JSONResponse(content=request.app.state.settings.to_public_dict())


@router.put("/api/settings", responses={
    200: {"description": "Settings updated"},
    400: {"description": "Invalid setting"},
    401: {"description": "Authentication required"},
    403: {"description": "CSRF token invalid"},
})
async def update_settings(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token),
):
    """Partial update of managed settings. ``ha_*`` changes rebuild the HA
    provider; an immich-timeout change recycles the Immich HTTP client — so the
    new values take effect without a restart. A blank ``ha_token`` is ignored
    (keeps the existing secret)."""
    from app.services.settings_service import SCHEMA
    from app.services.homeassistant_provider import build_ha_provider

    settings = request.app.state.settings
    unknown = [k for k in payload if k not in SCHEMA]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown setting(s): {', '.join(unknown)}")

    ha_changed = False
    immich_timeout_changed = False
    stream_cache_changed = False
    old_stream_mb = settings.get_int("stream_cache_mb")
    for key, value in payload.items():
        if key == "ha_token" and not value:
            continue  # blank = keep existing secret
        settings.set(key, value)
        if key.startswith("ha_"):
            ha_changed = True
        if key == "immich_http_timeout":
            immich_timeout_changed = True
        if key == "stream_cache_mb" and settings.get_int("stream_cache_mb") != old_stream_mb:
            stream_cache_changed = True

    if ha_changed:
        request.app.state.ha_provider = build_ha_provider(settings)
    if stream_cache_changed:
        fetcher = getattr(request.app.state, 'stream_fetcher', None)
        if fetcher:
            fetcher.set_cache_mb(settings.get_int("stream_cache_mb"))
    if immich_timeout_changed:
        provider = getattr(request.app.state, 'immich_provider', None)
        if provider:
            await provider.close()  # next call rebuilds the client with the new timeout

    return JSONResponse(content=settings.to_public_dict())


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
async def get_metrics(auth: Union[APIKey, User] = Depends(get_admin_auth)):
    """
    Return application metrics for observability. Admin-only: connection and
    group counters are operational details, not public data.

    Provides counters (monotonically increasing) and gauges (current values)
    for monitoring display group and WebSocket connection health.
    """
    return metrics.to_dict()


@router.get("/browse", responses={200: {"description": "Library browser (SPA)"}})
async def browse_images(request: Request):
    """Serve the SPA library browser. The React app fetches data from the fast
    stat-only /api/browse/* endpoints and handles auth client-side. (The old
    server-rendered tree — which opened every image file for metadata — and its
    ?format=json variant were removed with the legacy templates.)"""
    return spa_shell_or_503("/browse")


def _require_browse_auth(user: Optional[User]) -> None:
    """401 when OIDC is enabled and the caller is unauthenticated (the SPA's API
    client turns the 401 into a login redirect)."""
    if get_oidc_config().enabled and user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")


@router.get("/api/browse/folders", include_in_schema=True,
            responses={200: {"description": "Folders-only directory tree with counts"}})
async def browse_folders(request: Request, user: Optional[User] = Depends(get_optional_user)):
    """Fast folders-only tree (no per-file image opens) for the browse sidebar."""
    _require_browse_auth(user)
    index = await _get_browse_index()
    return JSONResponse(content={
        "root": index["root"],
        "total_folders": index["total_folders"],
        "total_files": index["total_files"],
        "tree": index["tree"],
    })


@router.get("/api/browse/files", include_in_schema=True,
            responses={200: {"description": "Paginated, filtered image list"}})
async def browse_files(
    request: Request,
    folder: str = Query("", description="Folder path to scope to (recursive); empty = whole library"),
    q: str = Query("", description="Case-insensitive filename substring filter"),
    filter: Literal["all", "favorites", "hidden"] = Query("all"),
    sort: Literal["name", "date", "size"] = Query("name", description="Sort key"),
    order: Literal["asc", "desc"] = Query("asc", description="Sort direction"),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    user: Optional[User] = Depends(get_optional_user),
):
    """Paginated image files from the cached index, filtered by folder/search/curation."""
    _require_browse_auth(user)
    index = await _get_browse_index()
    files: List[Dict[str, Any]] = index["files"]

    # Scope to a folder subtree (recursive). folder="trips" matches "trips/..".
    if folder:
        prefix = folder.strip("/") + "/"
        files = [f for f in files if f["path"].startswith(prefix)]

    if q:
        needle = q.lower()
        files = [f for f in files if needle in f["name"].lower()]

    if filter != "all":
        curation = request.app.state.curation_service
        wanted = curation.favorite_paths() if filter == "favorites" else curation.excluded_paths()
        files = [f for f in files if f["path"] in wanted]

    reverse = order == "desc"
    if sort == "name":
        files = sorted(files, key=lambda f: f["name"].lower(), reverse=reverse)
    elif sort == "date":
        files = sorted(files, key=lambda f: f.get("modified") or "", reverse=reverse)
    elif sort == "size":
        files = sorted(files, key=lambda f: f.get("size") or 0, reverse=reverse)

    total = len(files)
    page = files[offset:offset + limit]
    return JSONResponse(content={"total": total, "offset": offset, "files": page})


@router.get("/thumbnail/{image_path:path}", responses={
    200: {"description": "Thumbnail image (JPEG)", "content": {"image/jpeg": {}}},
    400: {"description": "Invalid image path", "content": {"application/json": {"example": {"error": "Invalid image path"}}}},
    401: {"description": "Authentication required"},
    404: {"description": "Image not found", "content": {"application/json": {"example": {"error": "Image not found"}}}}
})
@limiter.limit(THUMBNAIL_RATE_LIMIT)
async def get_thumbnail(
    request: Request,
    image_path: str,
    auth: Optional[Union[APIKey, User]] = Depends(get_image_auth),
):
    """
    Generate and serve a thumbnail for a specific image.

    Thumbnails are generated on-the-fly at 200x200px with crop mode.
    This endpoint is used by the browse interface for lazy-loaded thumbnails.
    Requires API key or session auth when OIDC is enabled.

    Args:
        image_path: Relative path to the image file (e.g., "earth/photo.jpg")

    Returns:
        Thumbnail image (200x200px, cropped to square)

    Examples:
    - GET /thumbnail/earth/photo.jpg
    - GET /thumbnail/space/nebula.png
    - GET /thumbnail/landscape/%D0%9C%D0%BE%D0%B6%D0%B6%D0%B5%D0%B2%D0%B5%D0%BB%D0%BE%D0%B2%D0%B0%D1%8F.jpg (Cyrillic)
    """
    # Check authentication if OIDC is enabled
    config = get_oidc_config()
    if config.enabled and auth is None:
        raise HTTPException(status_code=401, detail="Authentication required. Use API key or login.")

    # Check API key scope access
    if isinstance(auth, APIKey):
        if not check_scope_access(auth, request, image_path=image_path):
            raise HTTPException(status_code=403, detail="API key not authorized for this folder")

    try:
        # Decode URL-encoded characters (handles Cyrillic, Chinese, emoji, etc.)
        image_path = unquote(image_path)

        # Validate path using centralized security function
        full_path = resolve_image_path(image_path, image_service.image_dir)

        # Stream-mode asset: proxy Immich's own thumbnail (small memory LRU)
        stream_asset = image_service.stream_index.lookup(image_path) if image_service.stream_index else None
        if stream_asset is not None:
            try:
                thumb, thumb_type = await request.app.state.stream_fetcher.get_thumbnail(stream_asset)
            except StreamNotFound:
                return JSONResponse(status_code=404, content=error_response("Image not found"))
            except StreamUpstreamError as e:
                logger.warning(f"Immich thumbnail fetch failed for {image_path}: {e}")
                return JSONResponse(status_code=502, content=error_response("Image source unavailable"))
            return Response(content=thumb, media_type=thumb_type, headers={
                "Cache-Control": f"public, max-age={STATIC_CACHE_MAX_AGE}",
                "Content-Disposition": f'inline; filename="{_ascii_filename("thumb_" + Path(image_path).name)}"',
            })

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

        # Get thumbnail cache from app state
        thumbnail_cache: Optional[ThumbnailCache] = getattr(request.app.state, 'thumbnail_cache', None)

        # Check cache first
        if thumbnail_cache:
            cached = thumbnail_cache.get(image_path)
            if cached:
                return FileResponse(
                    cached,
                    media_type=thumbnail_cache.media_type,
                    headers={
                        "Cache-Control": f"public, max-age={STATIC_CACHE_MAX_AGE}",
                        "Content-Disposition": f'inline; filename="{_ascii_filename("thumb_" + Path(image_path).name)}"'
                    }
                )

        # Cache miss — generate, cache, then serve
        if thumbnail_cache:
            cache_path = await asyncio.get_running_loop().run_in_executor(
                image_executor, thumbnail_cache.generate, image_path
            )
            if cache_path:
                return FileResponse(
                    cache_path,
                    media_type=thumbnail_cache.media_type,
                    headers={
                        "Cache-Control": f"public, max-age={STATIC_CACHE_MAX_AGE}",
                        "Content-Disposition": f'inline; filename="{_ascii_filename("thumb_" + Path(image_path).name)}"'
                    }
                )

        # Fallback: generate on-the-fly without caching (cache not available)
        def process_thumbnail():
            image = image_service.load_image(full_path)
            thumbnail = image_service.resize_image(
                image,
                width=THUMBNAIL_SIZE,
                height=THUMBNAIL_SIZE,
                preserve_aspect=True,
                mode='crop'
            )
            if thumbnail.mode != 'RGB':
                thumbnail = thumbnail.convert('RGB')
            buffer = BytesIO()
            thumbnail.save(buffer, format='JPEG', quality=85, optimize=True)
            buffer.seek(0)
            return buffer

        buffer = await asyncio.get_running_loop().run_in_executor(image_executor, process_thumbnail)

        content = buffer.getvalue()
        content_length = len(content)
        buffer.seek(0)

        return StreamingResponse(
            iter_buffer_with_cleanup(buffer),
            media_type="image/jpeg",
            headers={
                "Content-Length": str(content_length),
                "Cache-Control": f"public, max-age={STATIC_CACHE_MAX_AGE}",
                "Content-Disposition": f'inline; filename="{_ascii_filename("thumb_" + Path(image_path).name)}"'
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
    401: {"description": "Authentication required"},
    404: {"description": "Image not found"}
})
async def get_image_metadata(
    request: Request,
    image_path: str,
    include_exif: bool = Query(False, description="Include EXIF data (default: false)"),
    auth: Optional[Union[APIKey, User]] = Depends(get_image_auth),
):
    """
    Get detailed metadata for a specific image, optionally including EXIF data.
    Requires API key or session auth when OIDC is enabled.

    Examples:
    - GET /metadata/landscape/photo.jpg
    - GET /metadata/landscape/photo.jpg?include_exif=true

    Returns JSON with:
    - Basic metadata: name, path, size, dimensions, format, modified date
    - EXIF data (if include_exif=true): camera info, settings, GPS, timestamps
    """
    # Check authentication if OIDC is enabled
    config = get_oidc_config()
    if config.enabled and auth is None:
        raise HTTPException(status_code=401, detail="Authentication required. Use API key or login.")

    # Check API key scope access
    if isinstance(auth, APIKey):
        if not check_scope_access(auth, request, image_path=image_path):
            raise HTTPException(status_code=403, detail="API key not authorized for this folder")

    try:
        # Decode URL-encoded characters (handles Cyrillic, Chinese, emoji, etc.)
        image_path = unquote(image_path)

        # Validate path using centralized security function
        full_path = resolve_image_path(image_path, image_service.image_dir)

        # Stream-mode asset: build metadata from the index row (no file to stat)
        stream_asset = image_service.stream_index.lookup(image_path) if image_service.stream_index else None
        if stream_asset is not None:
            dims = f"{stream_asset.width}x{stream_asset.height}" if stream_asset.width and stream_asset.height else "Unknown"
            return JSONResponse(content={
                "name": stream_asset.filename or Path(image_path).name,
                "path": stream_asset.path,
                "size": 0,
                "size_formatted": "Streamed",
                "dimensions": dims,
                "width": stream_asset.width,
                "height": stream_asset.height,
                "format": stream_asset.ext.upper(),
                "mode": None,
                "modified": stream_asset.taken_at,
                "type": "file",
                "stream": True,
                "is_favorite": stream_asset.is_favorite,
                "city": stream_asset.city,
                "country": stream_asset.country,
            })

        # Check if file exists (reject symlinks to match the other image
        # endpoints — they must not be reachable via symlinked paths)
        if not full_path.exists() or not full_path.is_file() or full_path.is_symlink():
            return JSONResponse(
                status_code=404,
                content=error_response("Image not found")
            )

        # Get metadata (runs in thread pool for EXIF extraction)
        def extract_metadata():
            return image_service.get_image_metadata(full_path, include_exif=include_exif)

        metadata = await asyncio.get_running_loop().run_in_executor(image_executor, extract_metadata)

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


def _image_request_cost(request: Request) -> int:
    """E-Ink processing is far more expensive than a plain serve — bill those
    requests more units against the shared per-IP image budget."""
    if request.query_params.get("eink", "").lower() == "true":
        return max(1, EINK_COST)
    return 1


@router.api_route("/api/image/{image_path:path}", methods=["GET", "HEAD"], responses={
    200: {"description": "Processed image", "content": {"image/*": {}}},
    304: {"description": "Not Modified (conditional request)"},
    400: {"description": "Invalid image path"},
    401: {"description": "Authentication required"},
    404: {"description": "Image not found"},
    429: {"description": "Rate limit exceeded"}
})
@limiter.limit(IMAGE_RATE_LIMIT, cost=_image_request_cost)
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
    auth: Optional[Union[APIKey, User]] = Depends(get_image_auth),
):
    """
    Serve a specific image with optional processing (resize, E-Ink, format conversion).

    Unlike /images/ static mount, this processes query parameters for dynamic image manipulation.
    Requires API key or session auth when OIDC is enabled.

    Examples:
    - /api/image/landscape/photo.jpg - Original image
    - /api/image/landscape/photo.jpg?eink=true - E-Ink processed with defaults
    - /api/image/landscape/photo.jpg?eink=true&saturation=3.0&contrast=1.5
    - /api/image/landscape/photo.jpg?width=800&height=600&mode=crop
    """
    # Check authentication if OIDC is enabled
    config = get_oidc_config()
    if config.enabled and auth is None:
        raise HTTPException(status_code=401, detail="Authentication required. Use API key or login.")

    # Check API key scope access
    if isinstance(auth, APIKey):
        if not check_scope_access(auth, request, image_path=image_path):
            raise HTTPException(status_code=403, detail="API key not authorized for this folder")

    try:
        # Decode URL-encoded path
        image_path = unquote(image_path)

        # Validate path using centralized security function
        full_path = resolve_image_path(image_path, image_service.image_dir)

        # Immich stream-mode assets have no file: they're fetched/rendered by the
        # StreamFetcher, and its render-cache key stands in for the mtime below.
        stream_asset = image_service.stream_index.lookup(image_path) if image_service.stream_index else None
        stream_data: Optional[bytes] = None
        if stream_asset is not None:
            fetcher = request.app.state.stream_fetcher
            mtime = fetcher.key_for(stream_asset)
            if mtime is None:
                return JSONResponse(status_code=404, content=error_response("Image not found"))
        else:
            # Check file exists and is not a symlink
            if not full_path.exists() or not full_path.is_file() or full_path.is_symlink():
                return JSONResponse(
                    status_code=404,
                    content=error_response("Image not found")
                )

            # mtime keys the server-side cache and feeds Last-Modified.
            mtime = full_path.stat().st_mtime

        # Stream fast path: nothing to transform, serve the pre-rendered bytes.
        if stream_asset is not None and not (width or height or eink or format):
            etag = hashlib.md5(f"{mtime}\x00original".encode()).hexdigest()
            if _etag_matches(request.headers.get("If-None-Match"), etag):
                return Response(status_code=304)
            rendered = await fetcher.get_bytes(stream_asset)
            headers = {
                "Cache-Control": f"public, max-age={STATIC_CACHE_MAX_AGE}",
                "Content-Disposition": f'inline; filename="{_ascii_filename(full_path.name)}"',
                "Content-Location": f"/api/image/{quote(image_path, safe='/')}",
                "ETag": f'"{etag}"',
                "Content-Length": str(len(rendered.data)),
                "Vary": "Accept",
            }
            if request.method == "HEAD":
                return Response(status_code=200, headers=headers, media_type=rendered.content_type)
            return Response(content=rendered.data, media_type=rendered.content_type, headers=headers)

        # Fast path: nothing to transform and the pixels are already upright —
        # stream the original bytes and skip the decode/re-encode entirely (no
        # CPU cost, no generational quality loss).
        source_format = full_path.suffix[1:].lower()
        wants_transform = bool(
            width or height or eink or (format and format != source_format)
        )
        if stream_asset is None and not wants_transform and source_format in ("jpeg", "jpg", "png", "webp", "gif", "bmp"):
            needs_transpose = await asyncio.get_running_loop().run_in_executor(
                image_executor, _needs_exif_transpose, full_path, mtime
            )
            if not needs_transpose:
                media_type = "image/jpeg" if source_format == "jpg" else f"image/{source_format}"
                etag = hashlib.md5(f"{image_path}\x00{mtime!r}\x00original".encode()).hexdigest()
                if _etag_matches(request.headers.get("If-None-Match"), etag):
                    return Response(status_code=304)
                headers = {
                    "Cache-Control": f"public, max-age={STATIC_CACHE_MAX_AGE}",
                    "Content-Disposition": f'inline; filename="{_ascii_filename(full_path.name)}"',
                    "Content-Location": f"/api/image/{quote(image_path, safe='/')}",
                    "ETag": f'"{etag}"',
                    "Last-Modified": formatdate(mtime, usegmt=True),
                    "Vary": "Accept",
                }
                if request.method == "HEAD":
                    headers["Content-Length"] = str(full_path.stat().st_size)
                    return Response(status_code=200, headers=headers, media_type=media_type)
                return FileResponse(full_path, media_type=media_type, headers=headers)

        cache_key = _processed_cache_key(
            image_path, mtime, width, height, preserve_aspect, mode, pad_color,
            eink, format, saturation, contrast, brightness, sharpen,
        )
        # The output is deterministic in the cache key, so the ETag can be
        # derived from it — conditional requests then 304 without ever
        # decoding the image, even on a processed-cache miss.
        etag = hashlib.md5(cache_key.encode()).hexdigest()
        if _etag_matches(request.headers.get("If-None-Match"), etag):
            return Response(status_code=304)

        # Process image in thread pool (CPU-intensive operations)
        def process():
            from app.utils.color_mapping import apply_spectra_6

            img = image_service.load_image(BytesIO(stream_data) if stream_data is not None else full_path)

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

            # Save to buffer, keeping the source colour profile so wide-gamut
            # photos don't shift hue after re-encoding.
            buffer = BytesIO()
            save_kwargs = {"quality": IMAGE_JPEG_QUALITY, "optimize": True}
            icc_profile = img.info.get("icc_profile")
            if icc_profile and output_format in ("jpeg", "png", "webp"):
                save_kwargs["icc_profile"] = icc_profile
            img.save(buffer, format=output_format.upper(), **save_kwargs)
            content = buffer.getvalue()

            content_type = f"image/{output_format}"
            filename_out = f"{full_path.stem}.{output_format}"
            return content, content_type, filename_out

        cached = processed_image_cache.get(cache_key)
        if cached is not None:
            content, content_type, filename_out = cached
        else:
            if stream_asset is not None:
                stream_data = (await fetcher.get_bytes(stream_asset)).data
            content, content_type, filename_out = await asyncio.get_running_loop().run_in_executor(
                image_executor, process
            )
            processed_image_cache.put(cache_key, (content, content_type, filename_out), len(content))

        content_length = len(content)

        # Build Content-Location URI for this specific image
        # RFC 7231: indicates a URI that can be used as an identifier for this resource
        # Must be a valid URI-reference — percent-encode the path to handle
        # spaces, Unicode, and other special characters in filenames.
        content_location = f"/api/image/{quote(image_path, safe='/')}"

        # Format Last-Modified as RFC 7231 HTTP-date (none for stream assets)
        last_modified = formatdate(mtime, usegmt=True) if stream_asset is None else None

        headers = {
            "Content-Length": str(content_length),
            # Disable caching for processed images to allow parameter changes
            "Cache-Control": "no-cache, no-store, must-revalidate" if eink else f"public, max-age={STATIC_CACHE_MAX_AGE}",
            "Content-Disposition": f'inline; filename="{_ascii_filename(filename_out)}"',
            "Content-Location": content_location,
            "ETag": f'"{etag}"',
            "Vary": "Accept",  # Response varies based on Accept header
        }
        if last_modified:
            headers["Last-Modified"] = last_modified

        # HEAD request - return headers only, no body
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers, media_type=content_type)

        return Response(content=content, media_type=content_type, headers=headers)

    except StreamNotFound:
        return JSONResponse(status_code=404, content=error_response("Image not found"))
    except StreamUpstreamError as e:
        logger.warning(f"Immich stream fetch failed for {image_path}: {e}")
        return JSONResponse(status_code=502, content=error_response("Image source unavailable"))
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
    401: {"description": "Authentication required"},
    404: {"description": "Image not found"}
})
@limiter.limit(BLUR_RATE_LIMIT)
async def get_blur_background(
    request: Request,
    image_path: str,
    blur_amount: int = Query(50, ge=0, le=100, description="Blur intensity (0-100)"),
    auth: Optional[Union[APIKey, User]] = Depends(get_image_auth),
):
    """
    Get a blurred version of an image as a base64 data URL.

    Used for creating blurred background behind letterboxed images.
    The blur is calculated based on image dimensions for consistent
    appearance across different image sizes.
    Requires API key or session auth when OIDC is enabled.

    Returns JSON with a 'blur_url' field containing the data URL.
    """
    # Check authentication if OIDC is enabled
    config = get_oidc_config()
    if config.enabled and auth is None:
        raise HTTPException(status_code=401, detail="Authentication required. Use API key or login.")

    # Check API key scope access
    if isinstance(auth, APIKey):
        if not check_scope_access(auth, request, image_path=image_path):
            raise HTTPException(status_code=403, detail="API key not authorized for this folder")

    try:
        # Decode URL-encoded path
        image_path = unquote(image_path)

        # Validate path using centralized security function
        full_path = resolve_image_path(image_path, image_service.image_dir)

        stream_asset = image_service.stream_index.lookup(image_path) if image_service.stream_index else None
        stream_data: Optional[bytes] = None
        if stream_asset is not None:
            stream_data = (await request.app.state.stream_fetcher.get_bytes(stream_asset)).data
        # Check file exists and is not a symlink
        elif not full_path.exists() or not full_path.is_file() or full_path.is_symlink():
            return JSONResponse(
                status_code=404,
                content=error_response("Image not found")
            )

        # Generate blur in thread pool (CPU-intensive)
        def generate_blur():
            img = image_service.load_image(BytesIO(stream_data) if stream_data is not None else full_path)
            return create_blur_background(img, blur_amount=blur_amount)

        blur_url = await asyncio.get_running_loop().run_in_executor(image_executor, generate_blur)

        return JSONResponse(content={"blur_url": blur_url})

    except StreamNotFound:
        return JSONResponse(status_code=404, content=error_response("Image not found"))
    except StreamUpstreamError as e:
        logger.warning(f"Immich stream fetch failed for {image_path}: {e}")
        return JSONResponse(status_code=502, content=error_response("Image source unavailable"))
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
    # Transition & Ken Burns settings (F3)
    transition_effect: Literal['none', 'fade', 'crossfade', 'slide', 'dissolve'] = Field('fade', description="Transition effect between images")
    transition_duration_ms: int = Field(500, ge=100, le=3000, description="Transition duration (ms)")
    ken_burns: bool = Field(False, description="Enable Ken Burns pan/zoom")
    ken_burns_intensity: int = Field(15, ge=0, le=50, description="Ken Burns max zoom/pan (%)")
    ken_burns_duration: int = Field(20, ge=5, le=120, description="Ken Burns duration (seconds)")
    # Photo caption overlay settings (F4)
    show_caption: bool = Field(False, description="Show photo caption overlay")
    caption_fields: List[str] = Field(default_factory=lambda: ['filename'], description="Caption fields (filename|date_taken|folder|location)")
    caption_position: Literal['bottom-left', 'bottom-right', 'top-left', 'top-right'] = Field('bottom-right', description="Caption position")
    # Night mode / schedule settings (F2)
    night_mode: bool = Field(False, description="Enable night-mode scheduling")
    night_mode_source: Literal['fixed', 'homeassistant'] = Field('fixed', description="Night schedule source")
    night_sleep_time: str = Field('23:00', max_length=5, description="Sleep time HH:MM (fixed)")
    night_wake_time: str = Field('07:00', max_length=5, description="Wake time HH:MM (fixed)")
    night_ha_sun_entity: str = Field('sun.sun', max_length=100, description="HA sun entity")
    night_action: Literal['blank', 'dim'] = Field('blank', description="Night action")
    night_dim_opacity: int = Field(20, ge=0, le=90, description="Night dim opacity (%)")
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
    # Transition & Ken Burns settings (F3)
    transition_effect: Optional[Literal['none', 'fade', 'crossfade', 'slide', 'dissolve']] = Field(None, description="Transition effect between images")
    transition_duration_ms: Optional[int] = Field(None, ge=100, le=3000, description="Transition duration (ms)")
    ken_burns: Optional[bool] = Field(None, description="Enable Ken Burns pan/zoom")
    ken_burns_intensity: Optional[int] = Field(None, ge=0, le=50, description="Ken Burns max zoom/pan (%)")
    ken_burns_duration: Optional[int] = Field(None, ge=5, le=120, description="Ken Burns duration (seconds)")
    # Photo caption overlay settings (F4)
    show_caption: Optional[bool] = Field(None, description="Show photo caption overlay")
    caption_fields: Optional[List[str]] = Field(None, description="Caption fields (filename|date_taken|folder|location)")
    caption_position: Optional[Literal['bottom-left', 'bottom-right', 'top-left', 'top-right']] = Field(None, description="Caption position")
    # Night mode / schedule settings (F2)
    night_mode: Optional[bool] = Field(None, description="Enable night-mode scheduling")
    night_mode_source: Optional[Literal['fixed', 'homeassistant']] = Field(None, description="Night schedule source")
    night_sleep_time: Optional[str] = Field(None, max_length=5, description="Sleep time HH:MM (fixed)")
    night_wake_time: Optional[str] = Field(None, max_length=5, description="Wake time HH:MM (fixed)")
    night_ha_sun_entity: Optional[str] = Field(None, max_length=100, description="HA sun entity")
    night_action: Optional[Literal['blank', 'dim']] = Field(None, description="Night action")
    night_dim_opacity: Optional[int] = Field(None, ge=0, le=90, description="Night dim opacity (%)")
    # Webhook settings
    webhooks: Optional[List[WebhookConfig]] = Field(None, description="Webhook configurations")


# =============================================================================
# Display Group API - Endpoints
# =============================================================================

def get_display_manager(request: Request) -> DisplayManager:
    """Get the DisplayManager from app state"""
    return request.app.state.display_manager


@router.get("/admin", include_in_schema=False)
async def admin_page(request: Request):
    """Serve the SPA admin dashboard (React handles auth via /auth/me)."""
    return spa_shell_or_503("/admin")


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
async def list_display_groups(request: Request, auth: Union[APIKey, User] = Depends(get_admin_auth)):
    """
    List all active display groups with their settings and connected browser count.

    Requires authentication.

    Returns:
        List of display groups with their configuration and status.
    """
    check_admin_scope(auth, "admin:display:monitor")
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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
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
    check_admin_scope(auth, "admin:display:manage")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    logger.info(f"User {actor} creating display group: {body.group_id}")

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
        # Transition & Ken Burns (F3)
        transition_effect=body.transition_effect,
        transition_duration_ms=body.transition_duration_ms,
        ken_burns=body.ken_burns,
        ken_burns_intensity=body.ken_burns_intensity,
        ken_burns_duration=body.ken_burns_duration,
        # Caption overlay (F4)
        show_caption=body.show_caption,
        caption_fields=body.caption_fields,
        caption_position=body.caption_position,
        # Night mode (F2)
        night_mode=body.night_mode,
        night_mode_source=body.night_mode_source,
        night_sleep_time=body.night_sleep_time,
        night_wake_time=body.night_wake_time,
        night_ha_sun_entity=body.night_ha_sun_entity,
        night_action=body.night_action,
        night_dim_opacity=body.night_dim_opacity,
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
            # Persist the endpoints (debounced, non-blocking)
            display_manager.schedule_save()

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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
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
    check_admin_scope(auth, "admin:display:manage")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    logger.info(f"User {actor} updating display group: {group_id}")

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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
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
    check_admin_scope(auth, "admin:display:manage")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    logger.info(f"User {actor} deleting display group: {group_id}")
    deleted = await display_manager.delete_group(group_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    return JSONResponse(content={"deleted": group_id})


class GroupCloneRequest(BaseModel):
    """Request body for cloning a display group (F5)."""
    new_group_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Identifier for the new (cloned) group"
    )


@router.post("/api/display/groups/{group_id}/clone", responses={
    201: {"description": "Group cloned"},
    400: {"description": "Target id already exists or invalid"},
    401: {"description": "Not authenticated"},
    404: {"description": "Source group not found"},
})
async def clone_display_group(
    request: Request,
    group_id: str,
    body: GroupCloneRequest,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """Clone an existing group's settings into a new group (F5)."""
    check_admin_scope(auth, "admin:display:manage")
    display_manager = get_display_manager(request)
    try:
        group = await display_manager.clone_group(group_id, body.new_group_id)
    except ValueError as e:
        msg = str(e)
        status = 404 if "not found" in msg else 400
        raise HTTPException(status_code=status, detail=msg)
    return JSONResponse(status_code=201, content=group.to_dict())


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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Pause a display group's image cycling.

    Requires authentication.
    Connected browsers will stay on their current image until resumed.
    """
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    logger.info(f"User {actor} pausing display group: {group_id}")
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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Resume a display group's image cycling.

    Requires authentication.
    """
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    logger.info(f"User {actor} resuming display group: {group_id}")
    success = await display_manager.play_group(group_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    return JSONResponse(content={"status": "playing", "group_id": group_id})


class AnnounceRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500, description="Message to show on the displays")
    position: Literal[
        'top-left', 'top-center', 'top-right',
        'center',
        'bottom-left', 'bottom-center', 'bottom-right',
    ] = 'bottom-left'
    theme: Literal['fade', 'solid', 'bubble', 'blur'] = 'bubble'
    size: Literal['s', 'm', 'l', 'xl'] = 'm'
    color: Literal['default', 'blue', 'green', 'amber', 'red', 'pink'] = 'default'
    scroll: bool = Field(False, description="Scroll the text as a single-line marquee instead of wrapping")
    duration_s: int = Field(15, ge=1, le=3600, description="Seconds to show before auto-clearing")


@router.post("/api/display/groups/{group_id}/announce", responses={
    200: {"description": "Announcement broadcast to the group"},
    401: {"description": "Not authenticated"},
    404: {"description": "Group not found"},
})
async def announce_display_group(
    request: Request,
    group_id: str,
    body: AnnounceRequest,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """Show a temporary text message on every screen in a group (auto-clears)."""
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    notified = await display_manager.announce(
        group_id, body.text, body.position, body.theme, body.duration_s,
        size=body.size, color=body.color, scroll=body.scroll,
    )
    if notified < 0:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")
    logger.info(f"User {actor} announced to '{group_id}' ({notified} displays): {body.text!r}")
    return JSONResponse(content={"status": "announced", "group_id": group_id, "notified": notified})


@router.delete("/api/display/groups/{group_id}/announce", responses={
    200: {"description": "Announcement cleared"},
    404: {"description": "Group not found"},
})
async def clear_display_group_announcement(
    request: Request,
    group_id: str,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """Immediately remove any active announcement from a group's screens."""
    check_admin_scope(auth, "admin:display:playback")
    display_manager = get_display_manager(request)
    if not await display_manager.clear_announcement(group_id):
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")
    return JSONResponse(content={"status": "cleared", "group_id": group_id})


@router.post("/api/display/groups/{group_id}/next", responses={
    200: {"description": "Skipped to next image"},
    401: {"description": "Not authenticated"},
    403: {"description": "CSRF token invalid"},
    404: {"description": "Group not found or no browsers connected"}
})
async def next_display_group(
    request: Request,
    group_id: str,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Skip to the next image immediately.

    Requires authentication.
    All browsers advance to their next image.
    """
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    logger.info(f"User {actor} skipping display group: {group_id}")
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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Go to the previous image.

    Requires authentication.
    Each browser goes back to its own previous image from history.
    Can be called multiple times to go further back (up to 100 images).
    """
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    logger.info(f"User {actor} going to previous for display group: {group_id}")
    success = await display_manager.previous_group(group_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found or no history available")

    return JSONResponse(content={"status": "rewound", "group_id": group_id})


@router.post("/api/display/groups/{group_id}/rewind-all")
async def rewind_all_group(
    group_id: str,
    request: Request,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Jump to the first image in history (rewind all the way).

    Requires authentication.
    This is a bulk operation - moves instantly to the beginning of history
    without stepping through each image.
    """
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    logger.info(f"User {actor} rewinding all for display group: {group_id}")
    success = await display_manager.rewind_all_group(group_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found or no history available")

    return JSONResponse(content={"status": "rewound_all", "group_id": group_id})


@router.post("/api/display/groups/{group_id}/forward-all")
async def forward_all_group(
    group_id: str,
    request: Request,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Jump to the most recent image (forward all the way).

    Requires authentication.
    This is a bulk operation - moves instantly to the most recent image
    without stepping through each one.
    """
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    logger.info(f"User {actor} forwarding all for display group: {group_id}")
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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Refresh the image list for a display group after files have changed.

    Requires authentication.
    Call this after adding/removing files to the group's image folders
    to update the available images without recreating the group.
    """
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    display_manager = get_display_manager(request)
    count = await display_manager.refresh_group_images(group_id)

    if count is None:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    logger.info(f"User {actor} refreshed images for group '{group_id}': {count} images")
    return JSONResponse(content={"group_id": group_id, "image_count": count})


@router.post("/api/display/groups/{group_id}/browsers/{browser_id}/jump")
async def jump_to_image(
    group_id: str,
    browser_id: str,
    request: Request,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Jump a specific browser to a specific image in its history.

    Requires authentication.
    The image must be in the browser's image_history or forward_history.
    """
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    body = await request.json()
    image_path = body.get("image_path")

    if not image_path:
        raise HTTPException(status_code=400, detail="image_path required")

    display_manager = get_display_manager(request)
    logger.info(f"User {actor} jumping browser {browser_id} in group {group_id} to {image_path}")
    success = await display_manager.jump_to_image(group_id, browser_id, image_path)

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Group, browser, or image not found in history"
        )

    return JSONResponse(content={"status": "jumped", "image_path": image_path})


@router.post("/api/display/groups/{group_id}/endpoints/{endpoint_id}/jump")
async def endpoint_jump_to_image(
    group_id: str,
    endpoint_id: str,
    request: Request,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Jump an endpoint to a specific image from its history.

    Works for disconnected endpoints (no browser required).
    The image must be in the endpoint's image_history or forward_history.
    """
    check_admin_scope(auth, "admin:display:playback")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    body = await request.json()
    image_path = body.get("image_path")

    if not image_path:
        raise HTTPException(status_code=400, detail="image_path required")

    display_manager = get_display_manager(request)
    logger.info(f"User {actor} jumping endpoint {endpoint_id} in group {group_id} to {image_path}")
    success = await display_manager.jump_endpoint_to_image(group_id, endpoint_id, image_path)

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Group, endpoint, or image not found in history"
        )

    return JSONResponse(content={"status": "jumped", "image_path": image_path})


@router.post("/api/display/groups/{group_id}/webhooks/{webhook_id}/test")
async def test_webhook(
    group_id: str,
    webhook_id: str,
    request: Request,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Test a specific webhook by sending a test payload.

    Requires authentication. Returns the webhook response status.
    """
    check_admin_scope(auth, "admin:display:playback")
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
        "message": "This is a test webhook from the Mosaic admin"
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
    # Per-endpoint presentation overrides (F7). Pass {} to clear all overrides.
    settings_override: Optional[Dict[str, Any]] = Field(None, description="Whitelisted setting overrides")


@router.get("/api/display/groups/{group_id}/endpoints", responses={
    200: {"description": "List of endpoints for the group"},
    404: {"description": "Group not found"},
})
async def list_endpoints(
    group_id: str = PathParam(...),
    request: Request = None,
    auth: Union[APIKey, User] = Depends(get_admin_auth)
):
    """List all endpoints for a display group."""
    check_admin_scope(auth, "admin:endpoints:monitor")
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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """Create a new persistent endpoint for a display group."""
    check_admin_scope(auth, "admin:endpoints:manage")
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

    # Persist the new endpoint (debounced, non-blocking)
    display_manager.schedule_save()

    # Notify admin clients of the change
    await display_manager._notify_admins({
        "type": "GROUP_UPDATED",
        "group": group.to_dict()
    })

    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    logger.info(f"User {actor} created endpoint '{body.endpoint_id}' in group '{group_id}'")

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
    auth: Union[APIKey, User] = Depends(get_admin_auth)
):
    """Get details of a specific endpoint."""
    check_admin_scope(auth, "admin:endpoints:monitor")
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    endpoint = group.endpoints.get(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    return JSONResponse(content=endpoint.to_dict())


@router.get("/api/display/groups/{group_id}/endpoints/{endpoint_id}/history", responses={
    200: {"description": "Full image history for the endpoint (newest first)"},
    404: {"description": "Group or endpoint not found"},
})
async def get_endpoint_history(
    group_id: str = PathParam(...),
    endpoint_id: str = PathParam(...),
    request: Request = None,
    auth: Union[APIKey, User] = Depends(get_admin_auth)
):
    """Full in-memory image history for an endpoint, newest first.

    Unlike the endpoint's to_dict() (which embeds only the last 10 for the live
    WS broadcast), this returns the complete history (up to MAX_IMAGE_HISTORY).
    """
    check_admin_scope(auth, "admin:endpoints:monitor")
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")

    endpoint = group.endpoints.get(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    return JSONResponse(content={
        "endpoint_id": endpoint.endpoint_id,
        "current_image": endpoint.current_image,
        # Both stacks are stored oldest->newest; present newest first. `forward`
        # holds images shown *after* the current one (when you've jumped back) —
        # they're preserved, not discarded, so the full session timeline is:
        #   forward (newest..), current, history (newest..)
        "history": list(reversed(endpoint.image_history)),
        "forward": list(reversed(endpoint.forward_history)),
        "history_count": len(endpoint.image_history),
        "forward_count": len(endpoint.forward_history),
    })


@router.get("/api/display/groups/{group_id}/endpoints/{endpoint_id}/caption", responses={
    200: {"description": "Server-built caption for the endpoint's current image"},
    404: {"description": "Group or endpoint not found"},
})
async def get_endpoint_caption(
    group_id: str = PathParam(...),
    endpoint_id: str = PathParam(...),
    request: Request = None,
    auth: Union[APIKey, User] = Depends(get_admin_auth)
):
    """The caption the kiosk shows for this endpoint's current image, built with
    the group's caption settings. Used by the admin display preview so it renders
    the exact same caption text without re-deriving it client-side."""
    check_admin_scope(auth, "admin:endpoints:monitor")
    display_manager = get_display_manager(request)
    group = await display_manager.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{group_id}' not found")
    endpoint = group.endpoints.get(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")

    caption = None
    if endpoint.current_image:
        caption = await display_manager.build_caption(endpoint.current_image, group.settings)
    return JSONResponse(content={
        "endpoint_id": endpoint.endpoint_id,
        "current_image": endpoint.current_image,
        "caption": caption,
    })


@router.get("/api/display/health", responses={
    200: {"description": "Per-endpoint display health summary"},
    401: {"description": "Authentication required"},
})
async def get_display_health(
    request: Request,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
):
    """Health of every display endpoint for dashboards / external monitors.

    Status per endpoint: `online` (a browser is connected with a fresh
    heartbeat), `idle` (connected but the heartbeat is aging), `stale` (heartbeat
    older than the cleanup timeout — about to be dropped), or `offline` (no live
    browser). Includes last image-load and cycle latency and a top-level rollup
    suitable for Uptime Kuma / Home Assistant / cron checks.
    """
    check_admin_scope(auth, "admin:display:monitor")
    display_manager = get_display_manager(request)
    settings = getattr(request.app.state, "settings", None)
    stale_after = settings.get_int("browser_stale_timeout") if settings else 60
    online_after = 45  # ~1.5x the 30s client heartbeat interval
    now = datetime.now(timezone.utc)

    groups_out = []
    total = online = offline = 0
    worst_load_ms = 0.0
    for group in display_manager.groups.values():
        # Newest live browser per endpoint (there is normally one).
        browser_by_ep = {}
        for b in group.browsers.values():
            browser_by_ep[b.endpoint_id] = b

        endpoints_out = []
        for ep in group.endpoints.values():
            total += 1
            b = browser_by_ep.get(ep.endpoint_id)
            if b is None:
                status = "offline"
                offline += 1
                heartbeat_age = None
                load_ms = cycle_ms = None
                uptime_s = None
            else:
                age = (now - b.last_heartbeat).total_seconds()
                heartbeat_age = round(age, 1)
                if age <= online_after:
                    status = "online"; online += 1
                elif age <= stale_after:
                    status = "idle"; online += 1
                else:
                    status = "stale"; offline += 1
                load_ms = b.last_load_ms
                cycle_ms = b.last_cycle_ms
                uptime_s = round((now - b.connected_at).total_seconds(), 1)
                if load_ms:
                    worst_load_ms = max(worst_load_ms, load_ms)
            endpoints_out.append({
                "endpoint_id": ep.endpoint_id,
                "name": ep.name,
                "status": status,
                "current_image": ep.current_image,
                "heartbeat_age_s": heartbeat_age,
                "last_load_ms": load_ms,
                "last_cycle_ms": cycle_ms,
                "uptime_s": uptime_s,
                "last_seen": ep.last_seen.isoformat() + "Z" if ep.last_seen else None,
            })

        groups_out.append({
            "group_id": group.group_id,
            "image_count": len(group.image_pool._all_images),
            "browser_count": len(group.browsers),
            "paused": group.paused,
            "night_paused": group.night_paused,
            "error": group.image_pool.error,
            "endpoints": endpoints_out,
        })

    return JSONResponse(content={
        "displays": total,
        "online": online,
        "offline": offline,
        "worst_load_ms": round(worst_load_ms, 1) if worst_load_ms else None,
        "groups": groups_out,
    })


@router.patch("/api/display/groups/{group_id}/endpoints/{endpoint_id}", responses={
    200: {"description": "Endpoint updated successfully"},
    404: {"description": "Group or endpoint not found"},
})
async def update_endpoint(
    group_id: str = PathParam(...),
    endpoint_id: str = PathParam(...),
    body: EndpointUpdateRequest = None,
    request: Request = None,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """Update an endpoint (e.g., rename or promote from anonymous to persistent)."""
    check_admin_scope(auth, "admin:endpoints:manage")
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
    override_changed = False
    if body.settings_override is not None:
        # Keep only whitelisted keys (F7)
        endpoint.settings_override = display_manager.filter_endpoint_override(body.settings_override)
        override_changed = True

    # Save config if endpoint is now persistent (includes promotions)
    if endpoint.is_persistent:
        display_manager.schedule_save()

    # Push merged settings to this endpoint's connected browsers immediately
    if override_changed:
        ep_settings = display_manager.build_endpoint_settings(group, endpoint)
        for browser in list(group.browsers.values()):
            if browser.endpoint_id == endpoint_id:
                try:
                    await browser.websocket.send_json({
                        "type": "SETTINGS_CHANGED",
                        "settings": ep_settings.to_dict()
                    })
                except Exception as e:
                    logger.debug(f"Override push failed for {browser.browser_id}: {e}")

    # Notify admin clients of the change
    await display_manager._notify_admins({
        "type": "GROUP_UPDATED",
        "group": group.to_dict()
    })

    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    logger.info(f"User {actor} updated endpoint '{endpoint_id}' in group '{group_id}'")

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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """Delete an endpoint. Cannot delete if a browser is currently connected."""
    check_admin_scope(auth, "admin:endpoints:manage")
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
        display_manager.schedule_save()

    # Notify admin clients of the change
    await display_manager._notify_admins({
        "type": "GROUP_UPDATED",
        "group": group.to_dict()
    })

    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    logger.info(f"User {actor} deleted endpoint '{endpoint_id}' from group '{group_id}'")

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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token),
):
    """
    Remove a specific image from a browser's history.

    Called by the admin UI when a thumbnail fails to load, indicating
    the image file no longer exists. Removes from both image_history
    and forward_history arrays while preserving order of remaining items.
    """
    check_admin_scope(auth, "admin:endpoints:manage")
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
        display_manager.schedule_save()
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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Request next image for a specific endpoint.

    Useful for on-demand mode (auto_cycle=false) to manually advance images.
    Works even when no browser is connected - updates the endpoint's state.
    """
    check_admin_scope(auth, "admin:endpoints:manage")
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

    # Notify admin dashboard of the change
    await display_manager._notify_admins({
        "type": "GROUP_UPDATED",
        "group": group.to_dict()
    })

    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    logger.info(f"User {actor} requested next image for endpoint '{endpoint_id}'")

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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Request previous image for a specific endpoint.

    Goes back in the endpoint's image history.
    """
    check_admin_scope(auth, "admin:endpoints:manage")
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

    # Notify admin dashboard of the change
    await display_manager._notify_admins({
        "type": "GROUP_UPDATED",
        "group": group.to_dict()
    })

    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    logger.info(f"User {actor} requested previous image for endpoint '{endpoint_id}'")

    return JSONResponse(content={
        "status": "ok",
        "endpoint_id": endpoint_id,
        "current_image": endpoint.current_image,
        "history_length": len(endpoint.image_history)
    })


@router.get("/display", include_in_schema=False)
async def display_page(request: Request):
    """Serve the SPA display kiosk. The React kiosk reads the group/endpoint
    query params client-side and drives everything over the WebSocket; use the
    admin API (POST endpoint navigation) for programmatic next/previous."""
    return spa_shell_or_503("/display")


async def _ws_admin_authorized(websocket: WebSocket) -> bool:
    """Authorize an admin WebSocket via session user OR an admin API key.

    Mirrors the dual auth (session + API key) used by every other admin
    endpoint. API keys may be supplied via the Authorization header or the
    ``?key=`` query param (used by the session-less display dashboards).
    """
    # Session user (OIDC) — full admin access
    if hasattr(websocket, "session") and websocket.session.get("user"):
        return True

    manager = getattr(websocket.app.state, "api_key_manager", None)
    if manager is None:
        return False

    # Extract a key from the Authorization header or the query param
    key: Optional[str] = None
    authorization = websocket.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        key = authorization[7:]
    else:
        key_param = websocket.query_params.get("key")
        if key_param and key_param.startswith("sk_"):
            key = key_param

    if not key:
        # Previously validated key stored in session
        key_id = websocket.session.get("api_key_id") if hasattr(websocket, "session") else None
        api_key = manager.get_key(key_id) if key_id else None
        if api_key and api_key.enabled:
            return _key_has_admin_monitor(api_key.scopes)
        return False

    api_key = await asyncio.get_running_loop().run_in_executor(
        None, manager.verify_key, key
    )
    if not api_key or not api_key.enabled:
        return False
    return _key_has_admin_monitor(api_key.scopes)


def _key_has_admin_monitor(scopes: Optional[List[str]]) -> bool:
    """True if the key may monitor admin display state (admin:display:monitor)."""
    scopes = scopes or []
    return (
        "*" in scopes
        or "admin:*" in scopes
        or "admin:display:*" in scopes
        or "admin:display:monitor" in scopes
    )


def _display_key_can_access(api_key: APIKey, group_id: str, group) -> bool:
    """True if an image API key may drive this display group's WebSocket.

    Access is by *folder*, mirroring what ``/api/image`` already enforces: any
    key that can load the group's images — via ``*``, ``folder:*``,
    ``group:{id}``, or a ``folder:{name}`` covering one of the group's folders —
    may join its display WS. (Endpoint-only scopes can't load images either, so
    they're excluded.) This deliberately does NOT require a group/endpoint scope,
    so folder-scoped and persistent-endpoint displays keep working.
    """
    scopes = api_key.scopes or []
    if not _has_valid_scopes(scopes):
        return False
    if "*" in scopes or "folder:*" in scopes or f"group:{group_id}" in scopes:
        return True
    folders = getattr(getattr(group, "settings", None), "folders", None)
    if folders:
        for folder in (f.strip() for f in folders.split(",")):
            if folder and f"folder:{folder}" in scopes:
                return True
    return False


async def _ws_display_authorized(websocket: WebSocket, group_id: str, group) -> bool:
    """Authorize a display WebSocket via session user OR an image API key.

    The key may arrive via the ``?key=`` query param (what the SPA display
    sends), an ``Authorization: Bearer`` header, or a previously-seeded session.
    Only consulted when OIDC is enabled (caller gates on that).
    """
    # Session user (OIDC login) — allowed.
    if hasattr(websocket, "session") and websocket.session.get("user"):
        return True

    manager = getattr(websocket.app.state, "api_key_manager", None)
    if manager is None:
        return False

    key: Optional[str] = None
    authorization = websocket.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        key = authorization[7:]
    else:
        key_param = websocket.query_params.get("key")
        if key_param and key_param.startswith("sk_"):
            key = key_param

    if key:
        # Argon2id verify is CPU-heavy — run off the event loop.
        api_key = await asyncio.get_running_loop().run_in_executor(
            None, manager.verify_key, key
        )
    else:
        key_id = websocket.session.get("api_key_id") if hasattr(websocket, "session") else None
        api_key = manager.get_key(key_id) if key_id else None

    if not api_key or not api_key.enabled:
        return False
    return _display_key_can_access(api_key, group_id, group)


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
        if not await _ws_admin_authorized(websocket):
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

    # Check the group exists, then authorize, before doing any real work. Both
    # rejections accept() first, then close() with a code: a close *before*
    # accept() rejects the handshake without a close frame, so the browser only
    # sees a generic 1006 and can't act on the specific code (or show the right
    # message). Accepting then immediately closing — before any browser is
    # registered or any data is sent — delivers the real 4004/4001 to onclose.
    group = await display_manager.get_group(group_id)
    if not group:
        await websocket.accept()
        await websocket.close(code=4004, reason=f"Group '{group_id}' not found")
        return

    # Authentication (only when OIDC is enabled): a session-less display must
    # present a valid API key (?key= / Authorization) that can access this group.
    # Mirrors /api/image so anonymous clients can't join a display WS and read
    # its image paths, captions, and settings.
    if get_oidc_config().enabled:
        if not await _ws_display_authorized(websocket, group_id, group):
            await websocket.accept()
            await websocket.close(code=4001, reason="Not authorized")
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

        # Merge this endpoint's overrides over the group settings (F7)
        ep_settings = display_manager.build_endpoint_settings(group, display_endpoint)

        # Send welcome message (include current night-mode state so a browser
        # connecting mid-night starts dark — F2)
        await websocket.send_json({
            "type": "WELCOME",
            "browser_id": browser.browser_id,
            "endpoint_id": browser.endpoint_id,
            "group_id": group_id,
            "browser_count": len(group.browsers),
            "settings": ep_settings.to_dict(),
            "night_active": group.night_paused
        })

        # Send initial image assignment (from endpoint's current_image)
        current_image = display_endpoint.current_image if display_endpoint else None
        if current_image:
            image_url = display_manager.build_image_url(
                current_image,
                ep_settings
            )
            blur_url = await display_manager.build_blur_url(
                current_image,
                ep_settings
            )
            await websocket.send_json({
                "type": "ASSIGN",
                "image_url": image_url,
                "blur_url": blur_url,
                "image_id": current_image,
                "caption": await display_manager.build_caption(current_image, ep_settings),
                "cycle": group.current_cycle
            })
        else:
            # No image available - send error to browser
            error_msg = group.image_pool.error or "No images configured for this group"
            await websocket.send_json({
                "type": "NO_IMAGES",
                "error": error_msg
            })

        # Re-show a still-live announcement so a browser that (re)connects
        # mid-announcement doesn't miss it; duration is trimmed to what's left.
        active = group.active_announcement
        if active:
            remaining = active["expires_at"] - time.monotonic()
            if remaining > 1:
                announcement = {k: v for k, v in active.items() if k != "expires_at"}
                announcement["duration_s"] = int(remaining)
                await websocket.send_json(announcement)

        # Handle incoming messages
        # Timeout is slightly longer than the client heartbeat interval (30s)
        # so a timeout means the client is dead or the connection is broken.
        RECEIVE_TIMEOUT = 45
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=RECEIVE_TIMEOUT
                )
                # Check if this browser was evicted by stale cleanup or
                # broadcast send failure (remove_browser called externally)
                if browser.evicted:
                    logger.info(f"Browser {browser.browser_id} evicted from {group_id}, closing handler")
                    break
                await display_manager.handle_browser_message(
                    group_id,
                    browser.browser_id,
                    data
                )
            except asyncio.TimeoutError:
                # No message in RECEIVE_TIMEOUT seconds — client likely dead
                logger.warning(f"Browser {browser.browser_id} receive timeout ({RECEIVE_TIMEOUT}s), closing handler")
                break
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
        # Always close the WebSocket to ensure the client gets onclose.
        # Belt-and-suspenders with the close in remove_browser — covers the
        # case where remove_browser was already called externally (no-op above).
        try:
            await websocket.close()
        except Exception:
            pass


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
    count: int = Field(50, ge=1, le=1_000_000, description="Number of images to pull (effective cap is the live max_immich_pull_count setting)")
    album_ids: Optional[list[str]] = Field(None, description="Pull from specific albums")
    album_filter_mode: str = Field("all", description="'all' (AND) or 'any' (OR) for albums")
    person_ids: Optional[list[str]] = Field(None, description="Filter by people")
    person_filter_mode: str = Field("all", description="'all' (AND) or 'any' (OR) for people")
    is_favorite: Optional[bool] = Field(None, description="Only favorites")
    taken_after: Optional[str] = Field(None, description="ISO date (YYYY-MM-DD)")
    taken_before: Optional[str] = Field(None, description="ISO date (YYYY-MM-DD)")
    media_type: str = Field("IMAGE", description="IMAGE or VIDEO")
    replace_existing: bool = Field(True, description="Clear folder before pulling")
    # Scheduling (F1)
    cron_expression: Optional[str] = Field(None, max_length=120, description="5-field cron expression (empty = unscheduled)")
    cron_timezone: Optional[str] = Field(None, max_length=64, description="IANA timezone (empty = inherit SCHEDULER_TIMEZONE)")
    # Curation interaction (F6)
    protect_favorites: bool = Field(True, description="Keep favorited files across replace_existing pulls")
    skip_excluded: bool = Field(True, description="Never re-download excluded assets")
    # Storage mode: "stream" keeps nothing on disk and renders images on demand
    storage_mode: Literal["download", "stream"] = Field("download", description="'download' to IMAGE_DIR or 'stream' (memory cache only)")
    stream_source: Literal["preview", "fullsize", "original"] = Field("preview", description="Immich rendition fetched in stream mode")
    stream_max_w: int = Field(0, ge=0, le=16384, description="Max width in stream mode (0 = source size)")
    stream_max_h: int = Field(0, ge=0, le=16384, description="Max height in stream mode (0 = source size)")
    stream_format: Literal["original", "jpeg", "png", "webp"] = Field("jpeg", description="Re-encode format in stream mode")
    stream_quality: int = Field(85, ge=1, le=100, description="JPEG/WebP quality in stream mode")


async def _forget_stream_pull(request: Request, pull_name: str, folder: Optional[str], drop_index: bool) -> None:
    """Clear a stream pull's cached renders and (optionally) its index rows, then
    refresh anything that listed its virtual folder."""
    fetcher = getattr(request.app.state, "stream_fetcher", None)
    if fetcher:
        fetcher.clear_pull(pull_name)
    if folder:
        processed_image_cache.clear_prefix(f"{folder}/")
    if drop_index and image_service.stream_index:
        image_service.stream_index.drop(pull_name)
        invalidate_browse_cache()
        if folder is not None:
            await request.app.state.display_manager.refresh_groups_by_folder(folder)


@router.get(
    "/api/immich/pulls",
    responses={
        200: {"description": "List of configured Immich pulls"},
        401: {"description": "Authentication required"},
    }
)
async def list_immich_pulls(
    request: Request,
    auth: Union[APIKey, User] = Depends(get_admin_auth)
):
    """
    List all configured Immich pull operations.

    Returns the list of pull configurations (API keys are masked).
    """
    check_admin_scope(auth, "admin:immich:monitor")
    provider = get_immich_provider(request)
    pulls = provider.list_pulls()

    # Mask API keys for security; attach schedule preview (F1)
    from app.services.pull_scheduler import default_timezone
    default_tz = default_timezone()
    stream_index = image_service.stream_index
    for pull in pulls:
        pull["stream_asset_count"] = (
            stream_index.count(pull["name"]) if stream_index and pull.get("storage_mode") == "stream" else 0
        )
        if pull.get("api_key"):
            pull["api_key"] = "***" + pull["api_key"][-4:] if len(pull["api_key"]) > 4 else "***"
        cron = pull.get("cron_expression")
        if cron:
            tz = cron_utils.resolve_tz(pull.get("cron_timezone"), default_tz)
            pull["schedule_description"] = cron_utils.describe(cron)
            try:
                pull["next_runs"] = [dt.isoformat() for dt in cron_utils.next_runs(cron, tz, 3)]
            except ValueError:
                pull["next_runs"] = []

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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Create or update an Immich pull configuration.

    Requires authentication and CSRF token.
    """
    check_admin_scope(auth, "admin:immich:manage")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    provider = get_immich_provider(request)
    logger.info(f"User {actor} creating/updating Immich pull: {body.name}")

    # Enforce the live max-pull-count setting (the Field bound is just a safety cap)
    max_pull = request.app.state.settings.get_int("max_immich_pull_count")
    if body.count > max_pull:
        raise HTTPException(status_code=400, detail=f"count exceeds the configured maximum ({max_pull})")

    # Get existing config to preserve API key if not provided
    api_key = body.api_key
    if not api_key:
        existing = provider.get_pull(body.name)
        if existing:
            api_key = existing.api_key
        else:
            raise HTTPException(status_code=400, detail="API key is required for new pulls")

    # Validate cron schedule up-front so a typo fails at save time (F1)
    cron_expression = (body.cron_expression or "").strip() or None
    cron_timezone = (body.cron_timezone or "").strip() or None
    if cron_expression:
        try:
            cron_utils.validate(cron_expression)
            if cron_timezone:
                cron_utils.validate_tz(cron_timezone)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if body.storage_mode == "stream" and body.media_type != "IMAGE":
        raise HTTPException(status_code=400, detail="Stream mode supports images only")

    # Preserve last_run across edits
    existing = provider.get_pull(body.name)
    last_run = existing.last_run if existing else None

    config = ImmichPullConfig(
        name=body.name,
        server_url=body.server_url,
        api_key=api_key,
        target_folder=body.target_folder,
        count=body.count,
        album_ids=body.album_ids,
        album_filter_mode=body.album_filter_mode,
        person_ids=body.person_ids,
        person_filter_mode=body.person_filter_mode,
        is_favorite=body.is_favorite,
        taken_after=body.taken_after,
        taken_before=body.taken_before,
        media_type=body.media_type,
        replace_existing=body.replace_existing,
        cron_expression=cron_expression,
        cron_timezone=cron_timezone,
        last_run=last_run,
        protect_favorites=body.protect_favorites,
        skip_excluded=body.skip_excluded,
        storage_mode=body.storage_mode,
        stream_source=body.stream_source,
        stream_max_w=body.stream_max_w,
        stream_max_h=body.stream_max_h,
        stream_format=body.stream_format,
        stream_quality=body.stream_quality,
    )

    provider.add_pull(config)
    provider.save_config()
    # Edited pull: cached renders may be stale; leaving stream mode (or moving
    # the folder) drops the index. Disk folders are never touched.
    old_folder = existing.target_folder.strip("/") if existing else None
    left_stream = bool(existing and existing.storage_mode == "stream" and (
        config.storage_mode != "stream" or config.target_folder.strip("/") != old_folder))
    await _forget_stream_pull(request, body.name, old_folder, drop_index=left_stream)
    # Re-install/remove the scheduled job for this pull (F1)
    scheduler = getattr(request.app.state, "pull_scheduler", None)
    if scheduler:
        scheduler.reload_pull(body.name)
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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Delete an Immich pull configuration.

    Requires authentication and CSRF token.
    """
    check_admin_scope(auth, "admin:immich:manage")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    provider = get_immich_provider(request)
    logger.info(f"User {actor} deleting Immich pull: {pull_name}")

    removed = provider.get_pull(pull_name)
    if provider.remove_pull(pull_name):
        provider.save_config()
        if removed and removed.storage_mode == "stream":
            await _forget_stream_pull(request, pull_name, removed.target_folder.strip("/"), drop_index=True)
        scheduler = getattr(request.app.state, "pull_scheduler", None)
        if scheduler:
            scheduler.reload_pull(pull_name)
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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Save current Immich pull configurations to file.

    Requires authentication and CSRF token.
    """
    check_admin_scope(auth, "admin:immich:manage")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    provider = get_immich_provider(request)
    logger.info(f"User {actor} saving Immich config")
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
    delta: bool = Query(False, description="Incremental pull: append only assets updated on the server since the last run (no folder wipe)"),
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Trigger an Immich pull operation.

    Starts the pull in the background and returns immediately. Pass ``delta=true``
    for an incremental pull (append-only, fetches just assets updated since the
    pull's last run). Use GET /api/immich/pull/{pull_name}/status to check progress.
    """
    check_admin_scope(auth, "admin:immich:trigger")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    provider = get_immich_provider(request)

    if not provider.get_pull(pull_name):
        raise HTTPException(status_code=404, detail=f"Pull '{pull_name}' not found")

    logger.info(f"User {actor} triggering Immich {'delta' if delta else 'full'} pull: {pull_name}")
    result = await provider.start_pull(pull_name, delta=delta)

    return JSONResponse(content={
        "status": "started",
        "pull_name": pull_name,
        "delta": delta,
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
    auth: Union[APIKey, User] = Depends(get_admin_auth)
):
    """
    Get the status of an Immich pull operation.

    Returns current progress if running, or final result if completed.
    """
    check_admin_scope(auth, "admin:immich:monitor")
    provider = get_immich_provider(request)

    if not provider.get_pull(pull_name):
        raise HTTPException(status_code=404, detail=f"Pull '{pull_name}' not found")

    result = provider.get_pull_status(pull_name)
    if result:
        return JSONResponse(content=result.to_dict())
    else:
        return JSONResponse(content={"status": "idle", "pull_name": pull_name})


@router.post(
    "/api/immich/pull/{pull_name}/cancel",
    responses={
        200: {"description": "Pull operation cancelled"},
        401: {"description": "Authentication required"},
        404: {"description": "Pull not found"},
    }
)
async def cancel_immich_pull(
    request: Request,
    pull_name: str,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Cancel a running Immich pull operation.

    Stops the pull gracefully, keeping any images already downloaded.
    """
    check_admin_scope(auth, "admin:immich:trigger")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    provider = get_immich_provider(request)

    if not provider.get_pull(pull_name):
        raise HTTPException(status_code=404, detail=f"Pull '{pull_name}' not found")

    logger.info(f"User {actor} cancelling Immich pull: {pull_name}")
    result = await provider.cancel_pull(pull_name)

    return JSONResponse(content={
        "status": "cancelled",
        "pull_name": pull_name,
        "result": result.to_dict(),
    })


@router.post(
    "/api/system/refresh",
    responses={
        200: {"description": "Caches invalidated and display groups refreshed"},
        401: {"description": "Authentication required"},
    }
)
async def refresh_caches(
    request: Request,
    folder: Optional[str] = Query(None, description="Limit the refresh to one library folder (relative path); omit to refresh everything"),
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """Invalidate stale caches and refresh display groups without a restart.

    Clears the processed-image and browse-index caches, the caption/blur caches
    and (for a scoped folder) the thumbnail cache, then re-scans affected display
    groups' image pools. Useful after files change on disk out-of-band, or to
    recover from a stale cache. Reuses the same invalidation path the file
    watcher runs on a folder change.
    """
    check_admin_scope(auth, "admin:display:manage")
    dm = request.app.state.display_manager
    tc = getattr(request.app.state, "thumbnail_cache", None)

    if folder:
        rel = folder.strip("/")
        processed_image_cache.clear_prefix(rel)
        if tc:
            tc.invalidate_folder(rel)
            asyncio.create_task(tc.pre_generate_folder(rel))
        invalidate_browse_cache()
        dm.clear_caption_cache()
        refreshed = await dm.refresh_groups_by_folder(rel)
        scope = rel
    else:
        # Global refresh: drop all processed/browse/caption caches. Thumbnails
        # are mtime-keyed (self-refreshing) so we don't force a full rebuild.
        processed_image_cache.clear()
        invalidate_browse_cache()
        dm.clear_caption_cache()
        refreshed = {}
        for group_id in list(dm.groups.keys()):
            count = await dm.refresh_group_images(group_id)
            if count is not None:
                refreshed[group_id] = count
        scope = None

    logger.info(f"Cache refresh ({scope or 'all'}) refreshed groups: {list(refreshed.keys())}")
    return JSONResponse(content={
        "status": "refreshed",
        "folder": scope,
        "groups_refreshed": refreshed,
    })


@router.get("/api/system/logs", responses={
    200: {"description": "Recent application log records (in-memory ring buffer)"},
    401: {"description": "Authentication required"},
})
async def get_logs(
    request: Request,
    level: Optional[str] = Query(None, description="Minimum level: DEBUG|INFO|WARNING|ERROR"),
    q: Optional[str] = Query(None, description="Case-insensitive substring filter (message or logger)"),
    limit: int = Query(500, ge=1, le=2000, description="Max records to return (newest kept)"),
    format: Optional[Literal["json", "text"]] = Query("json"),
    auth: Union[APIKey, User] = Depends(get_admin_auth),
):
    """The app's recent log records for the admin Logs panel. In-memory only
    (last ~2000 lines), oldest-first. `format=text` returns a downloadable dump."""
    check_admin_scope(auth, "admin:display:monitor")
    records = log_buffer.query(level=level, q=q, limit=limit)
    if format == "text":
        lines = [
            f"{datetime.fromtimestamp(r['ts'], tz=timezone.utc).isoformat()} {r['level']:<7} {r['logger']}: {r['message']}"
            for r in records
        ]
        return Response(
            content="\n".join(lines) + "\n",
            media_type="text/plain",
            headers={"Content-Disposition": 'attachment; filename="mosaic-logs.txt"'},
        )
    return JSONResponse(content={"records": records, "count": len(records)})


CONFIG_BUNDLE_VERSION = 1


@router.get("/api/system/config/export", responses={
    200: {"description": "Downloadable JSON config bundle"},
    401: {"description": "Authentication required"},
})
async def export_config(
    request: Request,
    include_keys: bool = Query(False, description="Include API keys (hash + encrypted secret) in the bundle"),
    auth: Union[APIKey, User] = Depends(get_admin_auth),
):
    """Download all configuration as one JSON bundle (groups, Immich pulls,
    settings, curation; API keys optional). Secrets remain encrypted; the
    secret_fingerprint records which derivation secret they need."""
    check_admin_scope(auth, "admin:display:manage")
    from app.auth.key_crypto import secret_fingerprint
    app = request.app
    # Flush in-memory state so the bundle reflects the live config, not a stale
    # store snapshot.
    app.state.display_manager.save_config()
    app.state.immich_provider.save_config()
    bundle: Dict[str, Any] = {
        "version": CONFIG_BUNDLE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "app_version": app.version,
        "secret_fingerprint": secret_fingerprint(),
        "groups": app.state.display_manager._store.load_all(),
        "immich_pulls": app.state.immich_provider._store.load_all(),
        "settings": app.state.settings.export_raw(),
        "curation": app.state.curation_service.export(),
    }
    if include_keys and hasattr(app.state, "api_key_manager"):
        bundle["api_keys"] = app.state.api_key_manager.export_rows()

    payload = json.dumps(bundle, indent=2)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="mosaic-config-{stamp}.json"'},
    )


@router.post("/api/system/config/import", responses={
    200: {"description": "Config restored"},
    400: {"description": "Invalid bundle"},
    401: {"description": "Authentication required"},
})
async def import_config(
    request: Request,
    bundle: Dict[str, Any] = Body(..., description="A bundle previously produced by the export endpoint"),
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token),
):
    """Restore configuration from an exported bundle and reload live state (no
    restart). Validates the whole bundle before writing anything."""
    check_admin_scope(auth, "admin:display:manage")
    from app.auth.key_crypto import secret_fingerprint

    # ---- validate before writing anything ----
    if not isinstance(bundle, dict):
        raise HTTPException(status_code=400, detail="Bundle must be a JSON object")
    version = bundle.get("version")
    if version != CONFIG_BUNDLE_VERSION:
        raise HTTPException(status_code=400, detail=f"Unsupported bundle version: {version!r}")
    section_types = {
        "groups": dict, "immich_pulls": dict, "settings": dict,
        "curation": dict, "api_keys": list,
    }
    for key, typ in section_types.items():
        if key in bundle and not isinstance(bundle[key], typ):
            raise HTTPException(status_code=400, detail=f"Section '{key}' has the wrong type")

    app = request.app
    restored: Dict[str, int] = {}

    if "groups" in bundle:
        app.state.display_manager._store.replace_all(bundle["groups"])
        # Authoritative reload (drops removed groups, re-applies changed settings).
        await app.state.display_manager.reload_from_store()
        restored["groups"] = len(bundle["groups"])
    if "immich_pulls" in bundle:
        app.state.immich_provider._store.replace_all(bundle["immich_pulls"])
        await app.state.immich_provider.load_config()
        restored["immich_pulls"] = len(bundle["immich_pulls"])
    if "settings" in bundle:
        app.state.settings.import_raw(bundle["settings"])
        # rebuild anything derived from settings
        from app.services.homeassistant_provider import build_ha_provider
        app.state.ha_provider = build_ha_provider(app.state.settings)
        restored["settings"] = len(bundle["settings"])
    if "curation" in bundle:
        app.state.curation_service.import_rows(bundle["curation"])
        restored["curation"] = len(bundle["curation"].get("curation", []))
    if "api_keys" in bundle and hasattr(app.state, "api_key_manager"):
        await app.state.api_key_manager.import_rows(bundle["api_keys"])
        restored["api_keys"] = len(bundle["api_keys"])
    # reschedule Immich cron jobs for the restored pulls
    if hasattr(app.state, "pull_scheduler"):
        app.state.pull_scheduler.reload_all()

    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    logger.info(f"User {actor} imported config bundle: {restored}")
    secret_match = bundle.get("secret_fingerprint") == secret_fingerprint()
    return JSONResponse(content={
        "status": "imported",
        "restored": restored,
        "secret_fingerprint_match": secret_match,
        "warning": None if secret_match else (
            "This bundle's encrypted secrets (Home Assistant token, Immich keys, API-key secrets) "
            "were encrypted with a different secret and cannot be decrypted on this instance. "
            "Re-enter them, or set API_KEY_ENCRYPTION_SECRET to match the source."
        ),
    })


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
    auth: Union[APIKey, User] = Depends(get_admin_auth)
):
    """
    Test connection to an Immich server.

    Returns server info if successful, error message if not.
    If api_key is not provided, looks up stored key from pull_name.
    """
    check_admin_scope(auth, "admin:immich:monitor")
    actor = auth.email if isinstance(auth, User) else f"api_key:{auth.name}"
    provider = get_immich_provider(request)

    # Get API key - use provided key or look up from stored pull
    api_key = body.api_key
    if not api_key and body.pull_name:
        existing = provider.get_pull(body.pull_name)
        if existing:
            api_key = existing.api_key

    if not api_key:
        return JSONResponse(content={"success": False, "error": "API key required"})

    logger.info(f"User {actor} testing Immich connection to: {body.server_url}")
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
    auth: Union[APIKey, User] = Depends(get_admin_auth)
):
    """
    List available albums from an Immich server.

    Useful for configuring album-based pulls.
    If api_key is not provided, looks up stored key from pull_name.
    """
    check_admin_scope(auth, "admin:immich:monitor")
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
    auth: Union[APIKey, User] = Depends(get_admin_auth)
):
    """
    List recognized people from an Immich server.

    Useful for configuring person-based pulls.
    If api_key is not provided, looks up stored key from pull_name.
    """
    check_admin_scope(auth, "admin:immich:monitor")
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
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """
    Create a new folder in the image directory.

    Used by the folder browser when setting up Immich pull target folders.
    """
    check_admin_scope(auth, "admin:immich:manage")
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

        # Ensure the path is within the base directory. Use relative_to (raises
        # ValueError on escape) rather than a string prefix check, which would
        # accept siblings like "/www/imagesX" for base "/www/images".
        try:
            full_path.relative_to(base_dir.resolve())
        except ValueError:
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
# Curation (favorite / hide-from-slideshow) — F6
# =============================================================================

class CurationToggleRequest(BaseModel):
    """Request body for setting a curation flag on an image path."""
    path: str = Field(..., min_length=1, max_length=1024, description="Image path relative to IMAGE_DIR")
    value: bool = Field(..., description="New flag value")


def _validate_curation_path(path: str) -> str:
    """Normalize + validate the image path; raises 400 on traversal."""
    try:
        # Stream-mode assets are virtual (no file); the index is the source of truth
        if image_service.stream_index and image_service.stream_index.lookup(path):
            return path.strip().strip("/")
        full = resolve_image_path(path, image_service.image_dir)
        return str(full.relative_to(image_service.image_dir))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image path")


async def _refresh_all_groups(request: Request) -> None:
    """Rebuild every group's image pool (used after an exclude toggle)."""
    display_manager = get_display_manager(request)
    for group_id in list(display_manager.groups.keys()):
        try:
            await display_manager.refresh_group_images(group_id)
        except Exception as e:
            logger.debug(f"Curation refresh failed for group {group_id}: {e}")


@router.get("/api/curation", responses={200: {"description": "Curation lists"}})
async def get_curation(request: Request, auth: Union[APIKey, User] = Depends(get_admin_auth)):
    """List all favorited and excluded image paths."""
    check_admin_scope(auth, "admin:display:monitor")
    curation = request.app.state.curation_service
    return JSONResponse(content=curation.list_all())


@router.get("/api/curation/state", responses={200: {"description": "Curation state for a path"}})
async def get_curation_state(request: Request, path: str, auth: Union[APIKey, User] = Depends(get_admin_auth)):
    """Return {favorite, excluded} for a single image path."""
    check_admin_scope(auth, "admin:display:monitor")
    rel = _validate_curation_path(path)
    return JSONResponse(content=request.app.state.curation_service.get_state(rel))


@router.post("/api/curation/favorite", responses={200: {"description": "Favorite flag updated"}})
async def set_curation_favorite(
    request: Request,
    body: CurationToggleRequest,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """Mark/unmark an image as a favorite."""
    check_admin_scope(auth, "admin:display:manage")
    rel = _validate_curation_path(body.path)
    curation = request.app.state.curation_service
    await asyncio.get_running_loop().run_in_executor(None, curation.set_favorite, rel, body.value)
    return JSONResponse(content={"path": rel, **curation.get_state(rel)})


@router.post("/api/curation/exclude", responses={200: {"description": "Exclude flag updated"}})
async def set_curation_exclude(
    request: Request,
    body: CurationToggleRequest,
    auth: Union[APIKey, User] = Depends(get_admin_auth),
    _csrf: None = Depends(verify_csrf_token)
):
    """Hide/unhide an image from the slideshow, then rebuild affected pools."""
    check_admin_scope(auth, "admin:display:manage")
    rel = _validate_curation_path(body.path)
    curation = request.app.state.curation_service
    await asyncio.get_running_loop().run_in_executor(None, curation.set_excluded, rel, body.value)
    # Excluded images must leave the live pool immediately
    await _refresh_all_groups(request)
    return JSONResponse(content={"path": rel, **curation.get_state(rel)})


# =============================================================================
# API Key Management
# =============================================================================

def get_api_key_manager(request: Request) -> APIKeyManager:
    """Get the APIKeyManager from app state."""
    return request.app.state.api_key_manager


class CreateAPIKeyRequest(BaseModel):
    """Request body for creating an API key."""
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable name for the key")
    scopes: Optional[List[str]] = Field(default=None, description="Permission scopes (default: ['images:read'])")


class UpdateAPIKeyRequest(BaseModel):
    """Request body for updating an API key."""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="New name")
    enabled: Optional[bool] = Field(None, description="Enable or disable the key")
    scopes: Optional[List[str]] = Field(None, description="New permission scopes")


@router.get(
    "/api/keys",
    responses={
        200: {"description": "List of API keys (hashes hidden)"},
        401: {"description": "Authentication required"},
    }
)
async def list_api_keys(
    request: Request,
    user: User = Depends(get_current_user),
    api_key_manager: APIKeyManager = Depends(get_api_key_manager),
):
    """
    List all API keys.

    Returns key metadata but not the actual key values (only prefix shown).
    """
    keys = api_key_manager.list_keys()
    return JSONResponse(content={
        "keys": [key.to_dict(include_hash=False) for key in keys]
    })


@router.post(
    "/api/keys",
    responses={
        200: {"description": "API key created - secret recoverable later via /secret"},
        401: {"description": "Authentication required"},
        403: {"description": "CSRF token invalid"},
    }
)
async def create_api_key(
    request: Request,
    body: CreateAPIKeyRequest,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token),
    api_key_manager: APIKeyManager = Depends(get_api_key_manager),
):
    """
    Create a new API key.

    IMPORTANT: The full key is only returned once in this response.
    Store it securely - it cannot be retrieved again.
    """
    full_key, api_key = api_key_manager.create_key(
        name=body.name,
        scopes=body.scopes,
    )

    # Save immediately
    await api_key_manager.save_config()

    return JSONResponse(content={
        "key": full_key,  # Only time the full key is shown
        "api_key": api_key.to_dict(include_hash=False),
        "warning": "Store this key securely. It is stored encrypted and can be revealed again from the keys list.",
    })


# Static routes must come BEFORE parameterized {key_id} routes
@router.get(
    "/api/keys/available-resources",
    responses={
        200: {"description": "Available groups, endpoints, and folders for scope configuration"},
        401: {"description": "Authentication required"},
    }
)
async def get_available_resources(
    request: Request,
    user: User = Depends(get_current_user),
):
    """
    Get all available groups, endpoints, and folders for API key scope configuration.

    Returns structured data for the UI to render scope selection checkboxes.
    """
    display_manager = get_display_manager(request)

    # Get groups with their endpoints and folders. Endpoints carry name +
    # persistence so the key editor can label them and offer explicit
    # endpoint scopes for saved (persistent) displays.
    groups = []
    for group in display_manager.groups.values():
        endpoints = [
            {
                "endpoint_id": ep.endpoint_id,
                "name": ep.name,
                "is_persistent": ep.is_persistent,
            }
            for ep in group.endpoints.values()
        ]
        folders = [f.strip() for f in (group.settings.folders or "").split(",") if f.strip()]
        groups.append({
            "group_id": group.group_id,
            "endpoints": endpoints,
            "folders": folders
        })

    # Get all available folders from image service
    all_folders = list(image_service.get_folder_list())

    return JSONResponse(content={
        "groups": groups,
        "folders": all_folders
    })


@router.post(
    "/api/keys/save",
    responses={
        200: {"description": "API keys saved to file"},
        401: {"description": "Authentication required"},
        403: {"description": "CSRF token invalid"},
    }
)
async def save_api_keys(
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token),
    api_key_manager: APIKeyManager = Depends(get_api_key_manager),
):
    """Save API keys configuration to file."""
    await api_key_manager.save_config()
    return JSONResponse(content={"status": "saved"})


@router.get(
    "/api/keys/{key_id}",
    responses={
        200: {"description": "API key details"},
        401: {"description": "Authentication required"},
        404: {"description": "API key not found"},
    }
)
async def get_api_key(
    key_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    api_key_manager: APIKeyManager = Depends(get_api_key_manager),
):
    """Get details of a specific API key."""
    api_key = api_key_manager.get_key(key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")

    return JSONResponse(content=api_key.to_dict(include_hash=False))


@router.get(
    "/api/keys/{key_id}/secret",
    responses={
        200: {"description": "Decrypted full key (admin session only)"},
        401: {"description": "Authentication required"},
        404: {"description": "API key not found"},
    }
)
async def reveal_api_key_secret(
    key_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    api_key_manager: APIKeyManager = Depends(get_api_key_manager),
):
    """Reveal the full ``sk_…`` secret for a key so the dashboard can build a
    display URL. Admin session only (never API-key auth). Returns ``key: null``
    for keys whose secret isn't recoverable (e.g. migrated hash-only keys)."""
    api_key = api_key_manager.get_key(key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")

    secret = api_key_manager.reveal_key(key_id)
    return JSONResponse(content={"key": secret, "recoverable": secret is not None})


@router.post(
    "/api/keys/{key_id}/rotate",
    responses={
        200: {"description": "Key rotated - new secret (old one revoked)"},
        401: {"description": "Authentication required"},
        403: {"description": "CSRF token invalid"},
        404: {"description": "API key not found"},
    }
)
async def rotate_api_key(
    key_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token),
    api_key_manager: APIKeyManager = Depends(get_api_key_manager),
):
    """Rotate a key's secret in place (id, name, scopes preserved). The old
    secret stops working immediately; the new secret is shown once here."""
    result = api_key_manager.rotate_key(key_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")

    full_key, api_key = result
    await api_key_manager.save_config()

    return JSONResponse(content={
        "key": full_key,  # Only time the new full key is shown
        "api_key": api_key.to_dict(include_hash=False),
        "warning": "Store this key securely. The previous secret no longer works.",
    })


@router.patch(
    "/api/keys/{key_id}",
    responses={
        200: {"description": "API key updated"},
        401: {"description": "Authentication required"},
        403: {"description": "CSRF token invalid"},
        404: {"description": "API key not found"},
    }
)
async def update_api_key(
    key_id: str,
    body: UpdateAPIKeyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token),
    api_key_manager: APIKeyManager = Depends(get_api_key_manager),
):
    """Update an API key's metadata (name, enabled status, scopes)."""
    api_key = api_key_manager.update_key(
        key_id=key_id,
        name=body.name,
        enabled=body.enabled,
        scopes=body.scopes,
    )

    if not api_key:
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")

    # Save changes
    await api_key_manager.save_config()

    return JSONResponse(content=api_key.to_dict(include_hash=False))


@router.delete(
    "/api/keys/{key_id}",
    responses={
        200: {"description": "API key revoked"},
        401: {"description": "Authentication required"},
        403: {"description": "CSRF token invalid"},
        404: {"description": "API key not found"},
    }
)
async def revoke_api_key(
    key_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_token),
    api_key_manager: APIKeyManager = Depends(get_api_key_manager),
):
    """
    Revoke (delete) an API key.

    The key will immediately stop working for all requests.
    """
    if not api_key_manager.revoke_key(key_id):
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")

    # Save changes
    await api_key_manager.save_config()

    return JSONResponse(content={"status": "revoked", "key_id": key_id})


# =============================================================================
# Catch-all route (MUST be last)
# =============================================================================

@router.api_route("/{folder_path:path}", methods=["GET", "HEAD"], responses={
    200: {"description": "Random image or web interface"},
    304: {"description": "Not Modified (conditional request)"},
    400: {"description": "Invalid folder path"},
    401: {"description": "Authentication required"},
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
    refresh: Optional[int] = Query(None, ge=5, le=3600, description="Auto-refresh interval in seconds (5-3600) for cycling through images"),
    auth: Optional[Union[APIKey, User]] = Depends(get_image_auth),
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

    Requires API key or session auth when OIDC is enabled.
    """
    # Serve the SPA shell for the home page once "/" is migrated. Strictly the
    # bare root in plain browser-navigation mode — never for the random-image
    # API, the refresh auto-cycle, or any sub-folder path, all of which must keep
    # their existing behavior (E-Ink devices depend on them).
    if folder_path == "" and api is None and refresh is None and (resp := spa_shell("/")):
        return resp

    # Check authentication if OIDC is enabled
    config = get_oidc_config()
    if config.enabled and auth is None:
        if api:
            # API mode - return 401 for programmatic clients
            raise HTTPException(status_code=401, detail="Authentication required. Use API key or login.")
        # Browser mode - redirect to login
        redirect_path = f"/{folder_path}" if folder_path else "/"
        return RedirectResponse(url=f"/auth/login?redirect={redirect_path}")

    # Check API key scope access for all requested folders
    if isinstance(auth, APIKey):
        # Collect all folders being accessed
        folders_to_check = []
        if folder_path:
            folders_to_check.append(folder_path)
        if folders:
            folders_to_check.extend([f.strip() for f in folders.split(',') if f.strip()])
        if also:
            folders_to_check.extend([f.strip() for f in also.split(',') if f.strip()])

        # Check access for each folder
        for folder in folders_to_check:
            if not check_scope_access(auth, request, image_path=folder):
                raise HTTPException(status_code=403, detail=f"API key not authorized for folder: {folder}")

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

            # Pick the image first: stream-mode bytes must be fetched here on the
            # loop, since an image_executor task can't wait on another one.
            loop = asyncio.get_running_loop()
            picked = await loop.run_in_executor(
                image_executor, image_service.get_random_image_path, final_folder_paths, recursive
            )
            stream_data = None
            stream_asset = image_service.stream_asset(picked) if picked else None
            if stream_asset is not None:
                try:
                    stream_data = (await request.app.state.stream_fetcher.get_bytes(stream_asset)).data
                except StreamNotFound:
                    return JSONResponse(status_code=404, content=error_response("Image not found"))
                except StreamUpstreamError as e:
                    logger.warning(f"Immich stream fetch failed for {picked}: {e}")
                    return JSONResponse(status_code=502, content=error_response("Image source unavailable"))

            # Run CPU-intensive image processing in thread pool to avoid blocking event loop
            result = await loop.run_in_executor(
                image_executor,
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
                recursive,
                picked,
                stream_data
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
        content_location = f"/api/image/{quote(relative_path, safe='/')}"

        # Format Last-Modified as RFC 7231 HTTP-date
        last_modified = formatdate(mtime, usegmt=True)

        # Check for conditional request (304 Not Modified)
        if_none_match = request.headers.get("If-None-Match")
        if _etag_matches(if_none_match, etag):
            buffer.close()
            return Response(status_code=304)

        headers = {
            "Content-Length": str(content_length),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Content-Disposition": f'inline; filename="{_ascii_filename(filename)}"',
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
            # Image URLs below are root-relative on purpose: this page is served
            # same-origin, so relative URLs resolve to the request origin and
            # satisfy the strict `img-src 'self'` CSP regardless of how the page
            # was reached (an absolute URL to a fixed host breaks when the
            # viewing origin differs from that host).
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
                image_url = f"/api/image/{relative_path}"
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

                # Build image URLs — URL-encode the relative path so filenames
                # with spaces or non-ASCII characters produce valid URLs.
                # (stream-mode assets have no file under /images/, so use /api/image)
                image_urls = [
                    f"{'/api/image' if image_service.stream_asset(file) else '/images'}/"
                    f"{quote(str(file.relative_to(image_service.image_dir)))}"
                    for file in image_files
                ]

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
