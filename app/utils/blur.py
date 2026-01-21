"""
Background Blur Utilities

Server-side Gaussian blur for creating blurred background images.
Inspired by Immich Kiosk's approach with normalized sigma calculation.
"""
import base64
import math
from io import BytesIO
from typing import Optional

from PIL import Image, ImageFilter

from app.utils.logging import logger


def calculate_normalized_sigma(width: int, height: int, blur_amount: int) -> float:
    """
    Calculate blur sigma normalized to image diagonal.

    This ensures consistent blur appearance across different image sizes.
    Based on Immich Kiosk's approach where blur is proportional to diagonal.

    Args:
        width: Image width in pixels
        height: Image height in pixels
        blur_amount: Blur intensity (0-100)

    Returns:
        Sigma value for Gaussian blur
    """
    diagonal = math.sqrt(width ** 2 + height ** 2)
    # Normalize: blur_amount of 50 gives ~2% of diagonal as sigma
    # blur_amount of 100 gives ~4% of diagonal
    base_ratio = blur_amount / 2500.0
    return diagonal * base_ratio


def create_blur_background(
    image: Image.Image,
    blur_amount: int = 50,
    quality: int = 60,
    max_dimension: int = 800
) -> str:
    """
    Create a blurred version of an image for use as background.

    The image is downscaled before blurring for performance, then encoded
    as a base64 data URL for embedding in HTML/CSS.

    Args:
        image: Source PIL Image
        blur_amount: Blur intensity (0-100+)
        quality: JPEG quality for output (1-100)
        max_dimension: Maximum width/height before blurring (for performance)

    Returns:
        Base64-encoded data URL of the blurred image (data:image/jpeg;base64,...)
    """
    # Work on a copy
    blurred = image.copy()

    # Convert to RGB if needed (for JPEG output)
    if blurred.mode not in ('RGB', 'L'):
        blurred = blurred.convert('RGB')

    # Downscale for performance if image is large
    # The blur will hide detail anyway, so no need for full resolution
    original_width, original_height = blurred.size
    if original_width > max_dimension or original_height > max_dimension:
        ratio = min(max_dimension / original_width, max_dimension / original_height)
        new_size = (int(original_width * ratio), int(original_height * ratio))
        blurred = blurred.resize(new_size, Image.Resampling.LANCZOS)

    # Calculate normalized sigma based on downscaled dimensions
    sigma = calculate_normalized_sigma(blurred.width, blurred.height, blur_amount)

    # Apply Gaussian blur
    blurred = blurred.filter(ImageFilter.GaussianBlur(radius=sigma))

    # Optionally darken slightly for better contrast with foreground
    # (Immich Kiosk reduces brightness by 20)
    # from PIL import ImageEnhance
    # enhancer = ImageEnhance.Brightness(blurred)
    # blurred = enhancer.enhance(0.8)

    # Encode as JPEG for smaller size
    buffer = BytesIO()
    blurred.save(buffer, format='JPEG', quality=quality, optimize=True)
    buffer.seek(0)

    # Return as data URL
    b64_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f"data:image/jpeg;base64,{b64_data}"


def should_skip_blur(image_fit: str, background_blur: bool) -> bool:
    """
    Determine if blur should be skipped based on settings.

    Blur is only useful when images are letterboxed (contain mode).
    When image_fit is "cover", the image fills the entire frame,
    so there's no visible background to blur.

    Args:
        image_fit: "contain" or "cover"
        background_blur: Whether blur is enabled in settings

    Returns:
        True if blur should be skipped
    """
    if not background_blur:
        return True

    if image_fit == "cover":
        return True

    return False
