"""Shared SlowAPI limiter.

Lives in its own module so both app.main (which attaches it to app.state and
registers the 429 handler) and app.api.routes (which decorates endpoints) can
import it without a circular import.

Limits are configurable via environment variables; set a limit to an empty
string to disable it for that class of endpoint.
"""
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Full-size image serving. The passthrough fast path makes plain requests
# cheap, but resize/format conversions still decode on the server.
IMAGE_RATE_LIMIT = os.environ.get("IMAGE_RATE_LIMIT", "120/minute")

# E-Ink processing is by far the most CPU-expensive transform. Each eink=true
# request bills this many units against the shared image budget (so with the
# defaults, pure E-Ink traffic is limited to 120/6 = 20 requests/minute).
EINK_COST = int(os.environ.get("EINK_RATE_COST", "6"))

# Thumbnails are cached on disk, but a cold cache generates on demand.
THUMBNAIL_RATE_LIMIT = os.environ.get("THUMBNAIL_RATE_LIMIT", "600/minute")

# Blur backgrounds are Gaussian blurs of full images (expensive, cached).
BLUR_RATE_LIMIT = os.environ.get("BLUR_RATE_LIMIT", "60/minute")
