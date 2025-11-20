"""
Spectra-6 E-Ink Color Mapping Utilities

Uses Pillow's built-in Floyd-Steinberg dithering for converting images to the
6-color Spectra palette used in E-Ink displays.

Palette colors:
- Red: (255, 0, 0)
- Green: (0, 255, 0)
- Blue: (0, 0, 255)
- Yellow: (255, 255, 0)
- Black: (0, 0, 0)
- White: (255, 255, 255)
"""

from PIL import Image


# Define the Spectra-6 color palette
# Using calibrated values instead of pure RGB for better E-Ink display accuracy
# These values are empirically derived to match actual Spectra-6 display output
# Source: Community calibration (mattdm @ Pimoroni forums)
SPECTRA_6_PALETTE = [
    (160, 32, 32),    # Red (#a02020)
    (96, 128, 80),    # Green (#608050)
    (80, 128, 184),   # Blue (#5080b8)
    (240, 224, 80),   # Yellow (#f0e050)
    (0, 0, 0),        # Black
    (255, 255, 255),  # White
]


def _create_palette_image() -> Image.Image:
    """
    Create a palette image for Spectra-6 colors.

    Returns:
        PIL Image in 'P' mode with Spectra-6 palette
    """
    palette_img = Image.new('P', (1, 1))

    # Flatten RGB tuples into a list and extend to 768 bytes (256 colors * 3 RGB)
    palette_data = []
    for color in SPECTRA_6_PALETTE:
        palette_data.extend(color)

    # Pad with zeros for remaining palette entries (256 - 6 = 250 unused colors)
    palette_data.extend([0] * (768 - len(palette_data)))

    palette_img.putpalette(palette_data)
    return palette_img


def floyd_steinberg_dither(image: Image.Image) -> Image.Image:
    """
    Apply Floyd-Steinberg dithering to convert an image to the Spectra-6 palette.

    Uses Pillow's built-in optimized Floyd-Steinberg implementation via quantize().
    This is significantly faster and more reliable than a custom implementation.

    Args:
        image: Input PIL Image

    Returns:
        Dithered PIL Image in RGB mode with Spectra-6 colors
    """
    # Convert to RGB if not already
    if image.mode != 'RGB':
        image = image.convert('RGB')

    # Create palette image with Spectra-6 colors
    palette_img = _create_palette_image()

    # Apply quantization with Floyd-Steinberg dithering
    # This uses Pillow's C-optimized implementation
    quantized = image.quantize(
        palette=palette_img,
        dither=Image.Dither.FLOYDSTEINBERG
    )

    # Convert back to RGB mode for consistency with API
    return quantized.convert('RGB')


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
