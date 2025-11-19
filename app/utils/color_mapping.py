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

    # Load pixel data
    pixels = image.load()
    width, height = image.size

    # Create a 2D array to store float values during processing
    # This allows us to accumulate error values
    pixel_array = []
    for y in range(height):
        row = []
        for x in range(width):
            r, g, b = pixels[x, y]
            row.append([float(r), float(g), float(b)])
        pixel_array.append(row)

    # Process each pixel
    for y in range(height):
        for x in range(width):
            # Get old pixel value (may have error from previous pixels added)
            old_pixel = (
                int(round(pixel_array[y][x][0])),
                int(round(pixel_array[y][x][1])),
                int(round(pixel_array[y][x][2]))
            )

            # Find nearest palette color
            new_pixel = find_nearest_color(old_pixel)

            # Set the new pixel value in the image
            pixels[x, y] = new_pixel

            # Calculate quantization error
            error_r = pixel_array[y][x][0] - new_pixel[0]
            error_g = pixel_array[y][x][1] - new_pixel[1]
            error_b = pixel_array[y][x][2] - new_pixel[2]

            # Distribute error to neighboring pixels (Floyd-Steinberg weights)
            if x + 1 < width:
                pixel_array[y][x + 1][0] += error_r * 7/16
                pixel_array[y][x + 1][1] += error_g * 7/16
                pixel_array[y][x + 1][2] += error_b * 7/16

            if y + 1 < height:
                if x > 0:
                    pixel_array[y + 1][x - 1][0] += error_r * 3/16
                    pixel_array[y + 1][x - 1][1] += error_g * 3/16
                    pixel_array[y + 1][x - 1][2] += error_b * 3/16

                pixel_array[y + 1][x][0] += error_r * 5/16
                pixel_array[y + 1][x][1] += error_g * 5/16
                pixel_array[y + 1][x][2] += error_b * 5/16

                if x + 1 < width:
                    pixel_array[y + 1][x + 1][0] += error_r * 1/16
                    pixel_array[y + 1][x + 1][1] += error_g * 1/16
                    pixel_array[y + 1][x + 1][2] += error_b * 1/16

    return image


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
