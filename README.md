# Screensaver Application

A modern image screensaver service built with Python/FastAPI, featuring image processing capabilities including resizing and E-Ink color mapping for Spectra-6 displays.

## Features

- **Random Image API**: Serve random images from a collection
- **Image Resizing**: Dynamic image resizing with flexible parameters
- **E-Ink Color Mapping**: Floyd-Steinberg dithering for 6-color Spectra displays
- **Web Interface**: Auto-rotating image slideshow (30-second intervals)
- **Docker-based**: Easy deployment with Docker Compose
- **Modern Stack**: Python 3.11 + FastAPI + Pillow
- **Security Hardened**: Non-root container, security headers, path traversal protection

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
cd screensaver
```

2. Configure environment variables:
```bash
cp .env.example .env
# Edit .env to set your BASE_URL
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

- Displays images in a 30-second rotation cycle
- Starts with a random image
- Full-screen black background
- Auto-refresh every 5 minutes

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
# Base URL for the application (optional)
# Leave empty for dynamic detection based on incoming requests
BASE_URL=https://screensaver.azureolympos.com

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
```

**Dimension Limits:** Width and height parameters are capped at 8192px to prevent memory exhaustion.

### Docker Compose

The application uses Docker Compose for orchestration. Key configuration:

```yaml
services:
  screensaver:
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
screensaver/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application entry point
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py           # API endpoints
│   ├── services/
│   │   ├── __init__.py
│   │   └── image_service.py    # Image processing logic
│   └── utils/
│       ├── __init__.py
│       ├── color_mapping.py    # Spectra-6 dithering
│       ├── path_security.py    # Path validation & traversal protection
│       └── logging.py          # Structured logging configuration
├── templates/
│   └── index.html              # Web interface template
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

# Set environment variables
export BASE_URL=http://localhost

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
docker compose logs -f screensaver

# View recent logs
docker compose logs --tail=100 screensaver
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
docker compose logs screensaver

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
- Monitor container resources: `docker stats screensaver`
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
