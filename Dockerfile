# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for Pillow and curl (for healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
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

# Create directory for images (will be mounted as volume)
RUN mkdir -p /www/images && chown appuser:appuser /www/images

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
