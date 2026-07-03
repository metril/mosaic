# Mosaic

Synchronized multi-display photo & signage engine, built with Python/FastAPI — drives a fleet of screens in perfect sync, each showing a different image from a curated collection, with image processing including resizing and E-Ink color mapping for Spectra-6 displays.

## Features

- **Random Image API**: Serve random images from a collection
- **Image Resizing**: Dynamic image resizing with flexible parameters
- **E-Ink Color Mapping**: Floyd-Steinberg dithering for 6-color Spectra displays
- **Web Interface**: Auto-rotating image slideshow (30-second intervals)
- **Synchronized Multi-Browser**: Display unique images across 100+ browsers in sync
- **Clock & Weather Overlays**: Display time, date, and weather from Home Assistant
- **Layout Modes**: Single image, split view portrait/landscape
- **Background Blur**: Blurred letterbox backgrounds for non-filling images
- **Burn-in Prevention**: Periodic dimming and pixel shifting for display protection
- **Webhooks**: Trigger external services on image cycles, settings changes, or intervals
- **Docker-based**: Easy deployment with Docker Compose
- **Modern Stack**: Python 3.11 + FastAPI + Pillow
- **Security Hardened**: Non-root container, security headers, path traversal protection
- **OIDC Authentication**: Protect admin endpoints with SSO integration
- **Rate Limiting**: Prevent resource exhaustion attacks
- **Metrics Endpoint**: Monitor connections, cycles, and errors at `/metrics`

## Tech Stack

- **Backend**: Python 3.11, FastAPI, Uvicorn
- **Image Processing**: Pillow (PIL)
- **Frontend**: Vanilla HTML/CSS/JavaScript with Jinja2 templates
- **Deployment**: Docker, Docker Compose

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Image directory mounted at `/mnt/usb-storage-1/images/` (or update `compose.yaml`)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd mosaic
```

2. Configure environment variables:
```bash
cp .env.example .env
# Edit .env for your deployment (OIDC, Home Assistant, limits — all optional)
```

3. Build and start the application:
```bash
docker compose build
docker compose up -d
```

4. Access the application:
- Web interface: `http://localhost/`
- API documentation: `http://localhost/docs` (FastAPI auto-generated)
- Health check: `http://localhost/health`

## API Endpoints

### Web Interface

**GET /**

Serves the web interface with auto-rotating images.

**Default Mode (slideshow):**
- Displays images in a 30-second rotation cycle
- Starts with a random image
- Full-screen black background
- Auto-refresh every 5 minutes

**Auto-Refresh Mode** (with `refresh` parameter):
- Shows a single random image per page load
- Page auto-reloads at specified interval
- Supports all image processing parameters (eink, resize, etc.)
- Better for large image collections (1000s of images)

**Query Parameters for Web Interface:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `refresh` | integer | Auto-refresh interval in seconds (5-3600). Enables auto-refresh mode |
| `folders` | string | Comma-separated folder paths to search |
| `also` | string | Additional folders (combine with path) |
| `recursive` | boolean | Search subfolders recursively |
| `width` | integer | Target width (requires `refresh` mode) |
| `height` | integer | Target height (requires `refresh` mode) |
| `eink` | boolean | Apply E-Ink color mapping (requires `refresh` mode) |
| `mode` | string | Resize mode: `fit` or `crop` (requires `refresh` mode) |

**Web Interface Examples:**

```bash
# Default slideshow mode
http://localhost/

# Auto-refresh every 30 seconds from earth folder
http://localhost/earth?refresh=30

# Auto-refresh with E-Ink processing
http://localhost/earth?refresh=30&eink=true

# Auto-refresh with resize and E-Ink
http://localhost/earth?refresh=30&width=800&height=600&eink=true

# Multiple folders with auto-refresh
http://localhost/?folders=earth,space&refresh=30&recursive=true
```

### Synchronized Multi-Browser Display

Display images across multiple browsers (20+) in sync, with each browser showing a different image that cycles at exactly the same time.

**Admin Dashboard:** `http://localhost/admin`

Create and manage display groups with all image processing options.

**Note:** When OIDC authentication is configured, admin endpoints require login. Unauthenticated users are redirected to `/auth/login`.

**Browser Display:** `http://localhost/display?group={group_id}`

Browsers connect via WebSocket for sub-second synchronization.

**Features:**
- Each browser displays a unique image (no duplicates)
- All browsers switch at exactly the same moment
- No image repeats until all have been shown
- All image processing options (eink, resize, etc.) available per group
- Admin dashboard for real-time group management
- Persistent endpoints for history preservation across reconnects

**Workflow:**
1. Create a display group via admin dashboard or API
2. Open browser tabs with the display URL
3. All browsers display synchronized, unique images
4. Press 'd' to toggle debug overlay (shows timing info)

**Admin API:**

```bash
# List all display groups
GET /api/display/groups

# Create a display group
POST /api/display/groups
{
    "group_id": "living-room",
    "duration": 30,
    "folders": "earth,space",
    "recursive": true,
    "eink": true,
    "width": 800,
    "height": 600
}

# Update group settings (broadcasts to all browsers)
PATCH /api/display/groups/living-room
{"duration": 60}

# Delete group (disconnects all browsers)
DELETE /api/display/groups/living-room
```

**Display Group Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `group_id` | string | required | Unique identifier |
| `duration` | int | 30 | Cycle duration (5-3600 seconds) |
| `auto_cycle` | bool | true | Auto-cycle images. When false, on-demand mode (manual advance only) |
| `folders` | string | null | Comma-separated folder paths |
| `recursive` | bool | false | Search subfolders |
| `width` | int | null | Target width |
| `height` | int | null | Target height |
| `preserve_aspect` | bool | true | Preserve aspect ratio |
| `mode` | string | "fit" | "fit", "fill", or "stretch" |
| `pad_color` | string | "black" | Letterbox color |
| `format` | string | null | Output format |
| **E-Ink Settings** ||||
| `eink` | bool | false | E-Ink color mapping |
| `saturation` | float | 2.5 | E-Ink saturation (0-10) |
| `contrast` | float | 1.2 | E-Ink contrast (0-5) |
| `brightness` | float | 1.1 | E-Ink brightness (0-5) |
| `sharpen` | bool | true | E-Ink sharpening |
| **Clock Settings** ||||
| `show_time` | bool | false | Display time overlay |
| `show_date` | bool | false | Display date overlay |
| `time_format` | string | "24" | "12" or "24" hour format |
| `date_format` | string | "DDDD, MMMM DD" | Date format string |
| `clock_source` | string | "client" | "client", "server", or "homeassistant" |
| `clock_position` | string | "bottom-left" | Corner position |
| **Weather Settings** ||||
| `show_weather` | bool | false | Display weather overlay |
| `weather_position` | string | "bottom-right" | Corner position |
| `weather_ha_entity` | string | null | Home Assistant weather entity ID |
| `weather_show_forecast` | bool | false | Show forecast data |
| **Layout Settings** ||||
| `layout` | string | "single" | "single", "splitview", "splitview-landscape" |
| **Background Settings** ||||
| `background_blur` | bool | false | Blur background for letterboxed images |
| `background_blur_amount` | int | 50 | Blur intensity (0-100) |
| `image_fit` | string | "contain" | "contain" or "cover" |
| **Burn-in Prevention** ||||
| `burn_in_protection` | bool | false | Enable burn-in prevention |
| `burn_in_interval` | int | 5 | Minutes between dimming (1-60) |
| `burn_in_duration` | int | 30 | Seconds dimmed (5-120) |
| `burn_in_opacity` | int | 70 | Opacity when dimmed (10-90%) |
| `burn_in_shift_enabled` | bool | true | Enable pixel shifting |
| `burn_in_shift_amount` | int | 5 | Pixels to shift (1-20) |
| `burn_in_shift_interval` | int | 60 | Seconds between shifts (10-300) |
| **Webhooks** ||||
| `webhooks` | array | [] | Webhook configurations (see below) |

### Persistent Endpoints

Display endpoints allow browsers to maintain image history across reconnections. Each endpoint tracks its own current image, history (for "previous" navigation), and forward history (for "next" after going back).

**Endpoint Types:**
- **Persistent** - Pre-created via API or admin UI. History preserved across server restarts.
- **Anonymous** - Auto-generated when browser connects without `?endpoint=` parameter. Cleaned up after 24 hours of inactivity (configurable via `ENDPOINT_CLEANUP`).

**Endpoint ID Rules:**
- Alphanumeric characters, dashes, and underscores only: `[a-zA-Z0-9_-]+`
- Maximum 64 characters
- Case-sensitive

**Connecting with Endpoint:**
```bash
# Connect browser with persistent endpoint (history preserved)
http://localhost/display?group=living-room&endpoint=kitchen-tv

# Connect without endpoint (anonymous endpoint auto-generated)
http://localhost/display?group=living-room
```

**Endpoint API:**
```bash
# List endpoints in a group
GET /api/display/groups/{group_id}/endpoints

# Create a persistent endpoint
POST /api/display/groups/{group_id}/endpoints
{
    "endpoint_id": "kitchen-tv",
    "name": "Kitchen TV"
}

# Get endpoint details
GET /api/display/groups/{group_id}/endpoints/{endpoint_id}

# Update endpoint (e.g., promote anonymous to persistent)
PATCH /api/display/groups/{group_id}/endpoints/{endpoint_id}
{
    "is_persistent": true,
    "name": "Kitchen TV"
}

# Delete endpoint (must have no connected browsers)
DELETE /api/display/groups/{group_id}/endpoints/{endpoint_id}
```

### On-Demand Mode

When `auto_cycle` is set to `false`, images don't automatically rotate. Instead, images only change when:
- URL action parameter: `/display?group=X&endpoint=Y&action=next`
- API endpoint: `POST /api/display/groups/{group}/endpoints/{endpoint}/next`
- Admin navigates via history thumbnails in the dashboard

This is useful for:
- Displays that should show the same image indefinitely
- Manual control over image changes
- Kiosk-style presentations
- IoT buttons or scripts that trigger image changes via URL

**Enable on-demand mode:**
```bash
POST /api/display/groups
{
    "group_id": "kiosk",
    "auto_cycle": false,
    "folders": "presentations"
}
```

**Navigate via URL:**
```bash
# Advance to next image
/display?group=kiosk&endpoint=my-display&action=next

# Go to previous image
/display?group=kiosk&endpoint=my-display&action=previous
```

**Navigate via API:**
```bash
# Advance to next image
POST /api/display/groups/{group_id}/endpoints/{endpoint_id}/next

# Go to previous image
POST /api/display/groups/{group_id}/endpoints/{endpoint_id}/previous
```

### Wallpanel & Iframe Embedding

The display endpoint can be embedded in iframes for use with Home Assistant's [Wallpanel](https://github.com/j-a-n/lovelace-wallpanel), Fully Kiosk Browser, or similar panel applications.

**Enable iframe embedding:**

Set the following environment variables:

```bash
# Allow display pages to be embedded in iframes
ALLOW_FRAME_EMBEDDING=true

# Optionally restrict which origins can embed (space-separated)
# Leave empty to allow any origin
FRAME_ANCESTORS=https://homeassistant.local:8123
```

**Wallpanel Configuration:**

1. Install Wallpanel from HACS
2. Point Wallpanel at your Mosaic display in its settings:
   - Set the display URL: `http://your-server/display?group=your-group&endpoint=wallpanel`
   - Enable full-screen mode for best experience
3. The display will connect via WebSocket and cycle images automatically

**Security Notes:**
- Only display routes (`/display`, `/api/image/*`) allow iframe embedding
- Admin dashboard remains protected with `X-Frame-Options: DENY`
- Use `FRAME_ANCESTORS` to restrict embedding to trusted origins only

### Clock & Weather Overlays

Display time, date, and weather information on the display.

**Clock Features:**
- 12 or 24-hour time format
- Customizable date format using Immich Kiosk tokens:
  - `DDDD` - Full weekday name (Monday)
  - `DDD` - Short weekday (Mon)
  - `DD` - Day of month (01-31)
  - `MMMM` - Full month name (January)
  - `MMM` - Short month (Jan)
  - `MM` - Month number (01-12)
  - `YYYY` - Full year (2026)
  - `YY` - Short year (26)
- Time sources: client browser, NTP server, or Home Assistant

**Weather Features:**
- Requires Home Assistant integration
- Displays current condition and temperature
- Optional forecast display
- Configure via `weather_ha_entity` (e.g., "weather.home")

**Position Options:** `bottom-left`, `bottom-right`, `top-left`, `top-right`

### Layouts

Control how images are displayed across the screen.

| Layout | Description |
|--------|-------------|
| `single` | One image fills the screen |
| `splitview` | Two images side by side (portrait orientation) |
| `splitview-landscape` | Two images stacked (landscape orientation) |

### Background Blur

When images don't fill the entire screen (letterboxed), a blurred version of the image can be shown as the background instead of solid black.

- **`background_blur`**: Enable/disable the feature
- **`background_blur_amount`**: Blur intensity (0-100, default 50)
- **`image_fit`**: `contain` (letterbox) or `cover` (fill, may crop)

Blur is only visible when `image_fit` is set to `contain`.

### Burn-in Prevention

Protect displays (especially OLED/E-Ink) from burn-in with periodic dimming and pixel shifting.

**Dimming:** Periodically reduces screen brightness to prevent static elements from burning in.
- `burn_in_interval`: How often to dim (minutes)
- `burn_in_duration`: How long to stay dimmed (seconds)
- `burn_in_opacity`: How dim (lower = darker)

**Pixel Shifting:** Subtly moves content to prevent static pixels.
- `burn_in_shift_enabled`: Enable/disable shifting
- `burn_in_shift_amount`: Pixels to shift each cycle
- `burn_in_shift_interval`: Seconds between shifts

### Webhooks

Sync groups can trigger webhooks on various events. Configure webhooks via the admin dashboard or API.

**Webhook Configuration:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | yes | Webhook endpoint URL |
| `enabled` | bool | no | Enable/disable (default: true) |
| `triggers` | array | no | Events: `settings_change`, `image_cycle`, `interval` |
| `payload_fields` | array | no | Data fields to include |
| `interval_minutes` | int | no | Minutes between triggers (1-1440) |
| `secret` | string | no | HMAC-SHA256 signing secret |
| `headers` | object | no | Custom HTTP headers |

**Trigger Types:**
- `settings_change`: When group settings are updated
- `image_cycle`: When images rotate
- `interval`: At fixed time intervals

**Payload Fields:**
- `group_id`: Sync group identifier
- `event_type`: Trigger type that fired
- `timestamp`: ISO 8601 timestamp
- `settings`: Full group settings object
- `current_image`: Currently displayed image paths
- `browser_count`: Number of connected browsers
- `cycle_number`: Current cycle count

**Example Payload:**
```json
{
  "group_id": "living-room",
  "event_type": "image_cycle",
  "timestamp": "2026-01-21T12:34:56+00:00",
  "browser_count": 3,
  "cycle_number": 42
}
```

**HMAC Signature:**
When `secret` is set, webhooks include `X-Webhook-Signature: sha256=<hex>` computed over the JSON payload.

**Webhook API Example:**
```bash
# Create group with webhook
POST /api/display/groups
{
    "group_id": "living-room",
    "duration": 30,
    "folders": "photos",
    "webhooks": [{
        "url": "https://example.com/webhook",
        "triggers": ["image_cycle"],
        "payload_fields": ["group_id", "event_type", "timestamp"],
        "secret": "my-secret",
        "headers": {"Authorization": "Bearer token"}
    }]
}

# Test a webhook
POST /api/display/groups/{group_id}/webhooks/{webhook_id}/test
```

### Random Image API

**GET /?api=random-image**

Returns a random image with optional processing.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `width` | integer | - | Target width in pixels |
| `height` | integer | - | Target height in pixels |
| `preserve_aspect` | boolean | `true` | Preserve aspect ratio when resizing |
| `mode` | string | `fit` | Resize mode: `fit` (letterbox) or `crop` (fill) |
| `pad_color` | string | `black` | Letterbox padding color (CSS: name, hex, rgb()) |
| `eink` | boolean | `false` | Apply Spectra-6 color mapping for E-Ink displays |
| `format` | string | - | Output format: `jpeg`, `png`, `webp`, `bmp`, `gif` |
| `saturation` | float | `2.5` | E-Ink saturation multiplier (0.0-10.0) |
| `contrast` | float | `1.2` | E-Ink contrast multiplier (0.0-5.0) |
| `brightness` | float | `1.1` | E-Ink brightness multiplier (0.0-5.0) |
| `sharpen` | boolean | `true` | Apply sharpening for E-Ink |
| `recursive` | boolean | `false` | Recursively search subfolders |
| `folders` | string | - | Comma-separated folder paths (alternative to path)

**Examples:**

```bash
# Get a random image (original size)
curl http://localhost/?api=random-image -o image.jpg

# Resize to 800x600, preserving aspect ratio (fit mode)
curl "http://localhost/?api=random-image&width=800&height=600" -o image.jpg

# Resize to exactly 800x600 using crop mode
curl "http://localhost/?api=random-image&width=800&height=600&mode=crop&preserve_aspect=false" -o image.jpg

# Apply E-Ink color mapping (Spectra-6 dithering)
curl "http://localhost/?api=random-image&eink=true" -o image.jpg

# Combined: Resize and apply E-Ink mapping
curl "http://localhost/?api=random-image&width=800&height=600&eink=true" -o image.jpg
```

**Response:**

- **200 OK**: Image binary data with appropriate headers
- **404 Not Found**: No images found in directory (JSON error)

**Headers:**
```
Content-Type: image/jpeg | image/png | image/bmp | image/gif | image/webp | image/tiff
Content-Disposition: inline; filename="<filename>"
Content-Location: /api/image/<path>
Content-Length: <size in bytes>
ETag: "<content hash>"
Last-Modified: <RFC 7231 date>
Cache-Control: no-cache, no-store, must-revalidate
Vary: Accept
```

**HEAD Requests:**

Both endpoints support HEAD method for metadata-only requests (no body).

**Conditional Requests (304 Not Modified):**

Send `If-None-Match: "<etag>"` header to check if content changed:
```bash
# First request - get ETag
curl -I "http://localhost/?api=random-image" | grep ETag
# ETag: "abc123..."

# Subsequent request - returns 304 if unchanged
curl -H 'If-None-Match: "abc123..."' "http://localhost/api/image/photo.jpg"
```

### Direct Image Access

**GET /api/image/{path}**

Serves a specific image with optional processing. Supports HEAD requests.

**Path Parameter:**
- `path`: Relative path to image from image directory

**Query Parameters:** Same as Random Image API

**Example:**
```bash
# Get a specific image
curl "http://localhost/api/image/photos/sunset.jpg"

# With processing
curl "http://localhost/api/image/photos/sunset.jpg?width=800&height=600&eink=true"
```

### Health Check

**GET /health**

Returns application health status and image count.

**Response:**
```json
{
  "status": "healthy",
  "image_count": 42,
  "image_directory": "/www/images"
}
```

### Metrics

**GET /metrics**

Returns application metrics for observability and monitoring.

**Response:**
```json
{
  "uptime_seconds": 3600.5,
  "counters": {
    "websocket_connections_total": 150,
    "websocket_disconnections_total": 140,
    "groups_created_total": 5,
    "groups_deleted_total": 2,
    "image_cycles_total": 720,
    "errors_total": 0
  },
  "gauges": {
    "active_groups": 3,
    "active_browsers": 10
  },
  "timestamps": {
    "start_time": "2026-01-08T10:00:00+00:00",
    "last_cycle_time": "2026-01-08T11:00:00+00:00"
  }
}
```

### Authentication

When OIDC is configured, admin endpoints require authentication. Unauthenticated users are redirected to the login page.

| Endpoint | Description |
|----------|-------------|
| **GET /auth/login** | Redirects to OIDC provider for authentication |
| **GET /auth/callback** | Handles OIDC callback after authentication |
| **GET /auth/logout** | Clears session and logs out user |
| **GET /auth/me** | Returns current user info (email, name) |

### API Keys

API keys allow display devices and external integrations to access images without browser-based OIDC login. When OIDC is enabled, image serving endpoints (`/api/image/*`, `/display`) require authentication via either session cookie or API key.

#### Creating API Keys

1. Open the admin dashboard at `/admin`
2. Click the key icon in the header
3. Enter a descriptive name (e.g., "Living Room Display")
4. Click **Create** and copy the key immediately - it will only be shown once

#### Using API Keys

**HTTP Header (recommended):**
```bash
curl -H "Authorization: Bearer sk_abc123..." https://your-server/api/image/photo.jpg
```

**Query Parameter:**
```
https://your-server/display?group=living-room&endpoint=tv&key=sk_abc123...
```

**Display URL with embedded key:**
```
https://your-server/display?group=living-room&endpoint=kitchen-display&key=sk_abc123...
```

#### Managing API Keys

From the admin dashboard (key icon in header):

- **Enable/Disable**: Temporarily disable a key without revoking it
- **Revoke**: Permanently delete a key (requires confirmation)
- **Save Config**: Persist changes to `config/api_keys.yaml`

#### Security Notes

- Keys are hashed with Argon2id before storage - the plaintext key cannot be recovered
- Keys are shown only once at creation time - store them securely
- Use descriptive names to identify which device uses each key
- Revoke keys immediately if compromised
- API keys are stored in `config/api_keys.yaml`

## Image Processing Details

### Resize Modes

#### Fit Mode (default)
- Resizes image to fit within specified dimensions
- Preserves aspect ratio by default
- When both width and height specified with `preserve_aspect=true`: adds letterbox padding
- Padding color configurable via `pad_color` parameter (default: black)

#### Crop Mode
- Resizes and crops image to exact dimensions
- Centers the crop area
- May cut off parts of the image

### Padding Color Formats

The `pad_color` parameter accepts CSS-style color values for letterbox padding:

| Format | Example | Description |
|--------|---------|-------------|
| Named colors | `black`, `white`, `coral` | ~40 CSS color names |
| Hex 6-digit | `ff0000` or `#ff0000` | Full RGB hex |
| Hex 3-digit | `f00` or `#f00` | Shorthand (expands to `ff0000`) |
| RGB function | `rgb(255,0,0)` | Decimal RGB values 0-255 |
| RGBA function | `rgba(255,0,0,128)` | Alpha value ignored |

**Supported Named Colors:**
`black`, `white`, `red`, `green`, `blue`, `yellow`, `cyan`, `magenta`, `gray`/`grey`, `navy`, `maroon`, `olive`, `purple`, `teal`, `silver`, `lime`, `aqua`, `fuchsia`, `orange`, `pink`, `brown`, `coral`, `gold`, `indigo`, `ivory`, `khaki`, `lavender`, `beige`, `tan`, `crimson`, `salmon`, `tomato`, `skyblue`, `steelblue`, `slategray`/`slategrey`, `darkgray`/`darkgrey`, `lightgray`/`lightgrey`, `darkblue`, `darkgreen`, `darkred`

### Spectra-6 E-Ink Color Mapping

The application implements Floyd-Steinberg dithering to convert images to the 6-color Spectra palette used in E-Ink displays.

**Palette Colors:**
- Red: `(255, 0, 0)`
- Green: `(0, 255, 0)`
- Blue: `(0, 0, 255)`
- Yellow: `(255, 255, 0)`
- Black: `(0, 0, 0)`
- White: `(255, 255, 255)`

**Processing Pipeline:**
1. Convert image to RGB mode
2. Apply Floyd-Steinberg dithering to map colors to palette
3. Distribute quantization error to neighboring pixels
4. (If resizing requested) Resize the dithered image

**Note:** E-Ink color mapping is applied **before** resizing to preserve dithering quality.

## Supported Image Formats

- JPEG/JPG
- PNG
- BMP
- GIF
- WebP
- TIFF/TIF

## Configuration

### Environment Variables

Configure via `.env` file (see `.env.example` for all options):

```bash
# Image directory (inside container)
IMAGE_DIR=/www/images

# Security limits
MAX_PATH_DEPTH=10
MAX_PATH_LENGTH=255

# E-Ink processing defaults
EINK_SATURATION=2.5
EINK_CONTRAST=1.2
EINK_BRIGHTNESS=1.1

# Logging
LOG_LEVEL=info

# Authentication (OIDC) - leave empty to disable auth
OIDC_ISSUER=https://keycloak.example.com/realms/myrealm
OIDC_CLIENT_ID=mosaic
OIDC_CLIENT_SECRET=your-client-secret
OIDC_REDIRECT_URI=https://mosaic.example.com/auth/callback
SESSION_SECRET=random-32-char-secret-for-sessions

# Security
ALLOWED_ORIGINS=https://mosaic.example.com
ALLOWED_WS_ORIGINS=https://mosaic.example.com
HTTPS_ONLY=true

# Iframe Embedding (for Wallpanel, Home Assistant, etc.)
ALLOW_FRAME_EMBEDDING=false
FRAME_ANCESTORS=

# Connection Limits
MAX_BROWSERS_PER_GROUP=100
MAX_GROUPS=50

# Display Endpoint Cleanup
# Supports: "30m", "24h", "7d", "1w", "2h30m", or plain seconds
# Set to "0" to disable cleanup of anonymous endpoints
ENDPOINT_CLEANUP=24h
```

**Dimension Limits:** Width and height parameters are capped at 8192px to prevent memory exhaustion.

### Docker Compose

The application uses Docker Compose for orchestration. Key configuration:

```yaml
services:
  mosaic:
    build: .
    ports:
      - "80:80"
    volumes:
      - /mnt/usb-storage-1/images:/www/images:ro  # Read-only image mount
    env_file:
      - .env
    restart: unless-stopped
    # Resource limits for stability
    mem_limit: 2g
    mem_reservation: 1g
    cpus: 2
    # Log rotation to prevent disk fill
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "3"
```

**Features:**
- **Health Check**: Built-in Docker HEALTHCHECK monitors `/health` endpoint every 30 seconds
- **Smart Compression**: GZip/Brotli/ZStd for JSON/HTML only (images excluded automatically)
- **Resource Limits**: Memory capped at 2GB, CPU limited to 2 cores
- **Log Rotation**: Logs capped at 100MB with 3 file rotation
- **Non-root User**: Container runs as unprivileged `appuser` (UID 1000)
- **Security Headers**: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection

**Customization:**
- Change port mapping: `ports: - "8080:80"` (host:container)
- Change image directory: Update volume mount path
- Adjust resource limits: Modify `mem_limit` and `cpus` values

## Development

### Project Structure

```
mosaic/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application entry point
│   ├── exceptions.py           # Custom exceptions and error helpers
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py           # API endpoints
│   ├── auth/                   # Authentication module
│   │   ├── __init__.py
│   │   ├── oidc.py             # OIDC client configuration
│   │   ├── dependencies.py     # Auth & CSRF dependencies
│   │   └── routes.py           # Login/logout endpoints
│   ├── services/
│   │   ├── __init__.py
│   │   ├── image_service.py    # Image processing logic
│   │   └── sync_manager.py     # Multi-browser sync manager
│   └── utils/
│       ├── __init__.py
│       ├── color_mapping.py    # Spectra-6 dithering
│       ├── path_security.py    # Path validation & traversal protection
│       ├── logging.py          # Structured logging configuration
│       └── metrics.py          # Application metrics collection
├── templates/
│   ├── index.html              # Web interface template
│   ├── sync.html               # Sync browser display
│   └── sync_admin.html         # Admin dashboard
├── static/                     # Static files (if any)
├── Dockerfile                  # Container definition
├── compose.yaml                # Docker Compose config
├── requirements.txt            # Python dependencies
├── .env                        # Environment variables
└── README.md                   # This file
```

### Running Locally (without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Create image directory (for testing)
mkdir -p /www/images
# Add some test images to /www/images/

# Run the application
python -m app.main

# Or use uvicorn directly
uvicorn app.main:app --host 0.0.0.0 --port 80
```

### Adding New Features

1. **New API endpoints**: Add to `app/api/routes.py`
2. **Image processing**: Extend `app/services/image_service.py`
3. **Color mapping algorithms**: Modify `app/utils/color_mapping.py`
4. **Frontend changes**: Edit `templates/index.html`

## Monitoring

### View Logs

```bash
# Follow logs
docker compose logs -f mosaic

# View recent logs
docker compose logs --tail=100 mosaic
```

### Container Status

```bash
# Check status
docker compose ps

# Restart container
docker compose restart

# Stop container
docker compose down

# Rebuild and restart
docker compose build && docker compose up -d
```

## Security

The application implements multiple layers of security:

### Path Security
- **Path Traversal Protection**: Blocks `..`, absolute paths, and null bytes
- **Symlink Detection**: Rejects symbolic links to prevent file system escapes
- **Filename Validation**: Blocks hidden files (dotfiles) and invalid characters
- **Depth Limiting**: Configurable maximum path depth (default: 10 levels)
- **Length Limiting**: Configurable maximum path length (default: 255 characters)

### HTTP Security Headers
All responses include security headers:
- `X-Content-Type-Options: nosniff` - Prevents MIME type sniffing
- `X-Frame-Options: DENY` - Prevents clickjacking
- `X-XSS-Protection: 1; mode=block` - XSS protection for legacy browsers
- `Content-Security-Policy` - Restricts script/style sources, prevents XSS

### Authentication & Authorization
- **OIDC Integration**: Admin endpoints protected by OpenID Connect (optional)
- **Session Management**: Secure cookie-based sessions (1 hour expiry)
- **CSRF Protection**: X-CSRF-Token header required for state-changing requests

### Rate Limiting
- **Admin API**: 5 requests/minute for group create/update/delete operations
- **Image Processing**: Rate limited to prevent resource exhaustion
- **429 Response**: Too Many Requests returned when limit exceeded

### WebSocket Security
- **Origin Validation**: Configurable allowed origins list (`ALLOWED_WS_ORIGINS`)
- **Connection Limits**: Max 100 browsers per group, 50 groups total (configurable)
- **Stale Connection Cleanup**: Automatic cleanup of disconnected browsers

### Container Security
- **Non-root User**: Application runs as `appuser` (UID 1000), not root
- **Read-only Volumes**: Image directory mounted as read-only (`:ro`)
- **Resource Limits**: Memory and CPU constraints prevent resource exhaustion
- **No Privileged Mode**: Container runs with minimal capabilities

### Input Validation
- **Dimension Limits**: Width/height capped at 8192px to prevent memory exhaustion
- **Image Validation**: Verifies image dimensions are non-zero before processing
- **CORS Configuration**: Properly configured without credentials for wildcard origins

## Performance Considerations

- **Image Count**: Tested with 50-5000 images
- **Image Sizes**: Handles various resolutions and file sizes
- **Processing Time**:
  - Simple resize: Fast (< 1 second)
  - E-Ink dithering: Depends on image size (larger images take longer)
  - Combined operations: Dithering time + resize time

**Optimization Tips:**
- Use smaller images for faster processing
- Consider caching processed images for repeated requests
- Monitor memory usage with large images

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker compose logs mosaic

# Verify image directory exists
ls -la /mnt/usb-storage-1/images/

# Check port availability
netstat -tulpn | grep :80
```

### No Images Found

- Verify image directory is mounted correctly
- Check file permissions (container needs read access)
- Ensure images have supported extensions

### Slow Performance

- Check image sizes (very large images take longer to process)
- Monitor container resources: `docker stats mosaic`
- Consider adding resource limits in `compose.yaml`

## Migration from PHP

This application replaces the legacy PHP 7 + Nginx setup with a modern Python/FastAPI stack.

**Key Differences:**
- ✅ Single container (vs. nginx + php containers)
- ✅ Modern Python 3.11 (vs. PHP 7)
- ✅ Image processing capabilities (resize, E-Ink mapping)
- ✅ Query parameter-based API (vs. single endpoint)
- ✅ Environment-based configuration (vs. hardcoded URLs)
- ✅ FastAPI auto-generated documentation (`/docs`)

**Migration Steps:**
1. Stop old containers: `docker compose down`
2. Update `compose.yaml` to new configuration
3. Build new image: `docker compose build`
4. Start new container: `docker compose up -d`

## License

[Specify your license]

## Contributing

[Specify contribution guidelines]

## Support

For issues or questions, please open an issue in the repository.
