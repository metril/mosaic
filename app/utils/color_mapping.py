"""
Spectra-6 E-Ink Color Mapping Utilities

Implements Floyd-Steinberg dithering for converting images to the 6-color
Spectra palette used in E-Ink displays.

Palette colors:
- Red: (255, 0, 0)
- Green: (0, 255, 0)
- Blue: (0, 0, 255)
- Yellow: (255, 255, 0)
- Black: (0, 0, 0)
- White: (255, 255, 255)
"""

from PIL import Image
import numpy as np
from typing import Tuple


# Define the Spectra-6 color palette
SPECTRA_6_PALETTE = [
    (255, 0, 0),      # Red
    (0, 255, 0),      # Green
    (0, 0, 255),      # Blue
    (255, 255, 0),    # Yellow
    (0, 0, 0),        # Black
    (255, 255, 255),  # White
]


def find_nearest_color(pixel: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """
    Find the nearest color in the Spectra-6 palette using Euclidean distance.

    Args:
        pixel: RGB tuple (r, g, b)

    Returns:
        RGB tuple of the nearest palette color
    """
    min_distance = float('inf')
    nearest_color = SPECTRA_6_PALETTE[0]

    for palette_color in SPECTRA_6_PALETTE:
        # Calculate Euclidean distance in RGB space
        distance = sum((p - c) ** 2 for p, c in zip(pixel, palette_color)) ** 0.5

        if distance < min_distance:
            min_distance = distance
            nearest_color = palette_color

    return nearest_color


def floyd_steinberg_dither(image: Image.Image) -> Image.Image:
    """
    Apply Floyd-Steinberg dithering to convert an image to the Spectra-6 palette.

    Floyd-Steinberg error diffusion distributes quantization error to neighboring pixels:
    - 7/16 to the pixel on the right
    - 3/16 to the pixel below-left
    - 5/16 to the pixel below
    - 1/16 to the pixel below-right

    Args:
        image: Input PIL Image in RGB mode

    Returns:
        Dithered PIL Image in RGB mode with Spectra-6 colors
    """
    # Convert to RGB if not already
    if image.mode != 'RGB':
        image = image.convert('RGB')

    # Convert to numpy array for efficient processing
    img_array = np.array(image, dtype=np.float32)
    height, width = img_array.shape[:2]

    # Process each pixel
    for y in range(height):
        for x in range(width):
            old_pixel = tuple(img_array[y, x].astype(int))
            new_pixel = find_nearest_color(old_pixel)

            # Set the new pixel value
            img_array[y, x] = new_pixel

            # Calculate quantization error
            error = img_array[y, x] - np.array(old_pixel, dtype=np.float32)

            # Distribute error to neighboring pixels (Floyd-Steinberg weights)
            if x + 1 < width:
                img_array[y, x + 1] += error * 7/16

            if y + 1 < height:
                if x > 0:
                    img_array[y + 1, x - 1] += error * 3/16

                img_array[y + 1, x] += error * 5/16

                if x + 1 < width:
                    img_array[y + 1, x + 1] += error * 1/16

    # Clip values to valid range and convert back to uint8
    img_array = np.clip(img_array, 0, 255).astype(np.uint8)

    # Convert back to PIL Image
    return Image.fromarray(img_array, mode='RGB')


def apply_spectra_6(image: Image.Image) -> Image.Image:
    """
    Apply Spectra-6 color mapping to an image using Floyd-Steinberg dithering.

    This is the main entry point for E-Ink color conversion.

    Args:
        image: Input PIL Image

    Returns:
        Image converted to Spectra-6 palette with Floyd-Steinberg dithering
    """
    return floyd_steinberg_dither(image)
