# syntax=docker/dockerfile:1

# -----------------------------------------------------------------------------
# Stage 1: Build the React (Vite) SPA in web/ -> dist/
# -----------------------------------------------------------------------------
FROM node:20-slim AS spa-builder
WORKDIR /build

# Install deps first for better layer caching
COPY web/package.json web/package-lock.json ./
RUN npm ci

# Build the SPA. vite.config.ts has outDir '../app/static/spa'; from WORKDIR
# /build that resolves to /app/static/spa in this stage.
COPY web/ ./
RUN npm run build
# Output: /app/static/spa/{index.html, assets/*}

# -----------------------------------------------------------------------------
# Stage 2: Python runtime
# -----------------------------------------------------------------------------
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Runtime dependencies: just curl for the healthcheck. Pillow ships manylinux
# wheels with bundled libjpeg/zlib/libpng, so no image-library dev headers are
# needed in the runtime image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user for security
RUN useradd -m -u 1000 appuser

# Copy application code and set ownership
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser templates/ ./templates/
COPY --chown=appuser:appuser config/ ./config/
# One-shot maintenance scripts (API key YAML->SQLite migration, re-encryption)
COPY --chown=appuser:appuser scripts/ ./scripts/

# Copy the built SPA into the static directory served by FastAPI
COPY --from=spa-builder --chown=appuser:appuser /app/static/spa/ ./app/static/spa/

# Create directory for images (will be mounted as volume)
RUN mkdir -p /www/images && chown appuser:appuser /www/images

# Create directory for thumbnail cache (will be mounted as volume)
RUN mkdir -p /www/thumbnails && chown appuser:appuser /www/thumbnails

# Create directory for config files (will be mounted as volume)
RUN mkdir -p /app/config && chown appuser:appuser /app/config

# Switch to non-root user
USER appuser

# Expose port 80
EXPOSE 80

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Health check - verify application is responding
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost/health || exit 1

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
