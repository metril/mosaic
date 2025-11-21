"""
Image Processing Service

Handles image selection, loading, resizing, and color mapping operations.
"""

import os
import random
from pathlib import Path
from typing import Optional, Tuple, Literal
from PIL import Image
from io import BytesIO

from app.utils.color_mapping import apply_spectra_6


# Supported image formats
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tiff', '.tif'}

# Default image directory
IMAGE_DIR = Path('/www/images')


class ImageService:
    """Service for managing and processing images."""

    def __init__(self, image_dir: Path = IMAGE_DIR):
        """
        Initialize the image service.

        Args:
            image_dir: Path to the directory containing images
        """
        self.image_dir = image_dir

    def get_image_files(self) -> list[Path]:
        """
        Get a list of all supported image files in the image directory.

        Returns:
            List of Path objects for image files
        """
        if not self.image_dir.exists():
            return []

        image_files = []
        for file_path in self.image_dir.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_FORMATS:
                image_files.append(file_path)

        return image_files

    def get_random_image_path(self) -> Optional[Path]:
        """
        Get a random image file path from the image directory.

        Returns:
            Path to a random image file, or None if no images found
        """
        image_files = self.get_image_files()
        if not image_files:
            return None

        return random.choice(image_files)

    def load_image(self, image_path: Path) -> Image.Image:
        """
        Load an image from disk.

        Args:
            image_path: Path to the image file

        Returns:
            PIL Image object
        """
        return Image.open(image_path)

    def resize_image(
        self,
        image: Image.Image,
        width: Optional[int] = None,
        height: Optional[int] = None,
        preserve_aspect: bool = True,
        mode: Literal['fit', 'crop'] = 'fit'
    ) -> Image.Image:
        """
        Resize an image with specified parameters.

        Args:
            image: Input PIL Image
            width: Target width (optional)
            height: Target height (optional)
            preserve_aspect: Whether to preserve aspect ratio
            mode: Resize mode - 'fit' (letterbox) or 'crop' (fill)

        Returns:
            Resized PIL Image
        """
        # If no dimensions specified, return original
        if width is None and height is None:
            return image

        original_width, original_height = image.size

        # Calculate target dimensions
        if preserve_aspect:
            target_width, target_height = self._calculate_aspect_preserving_size(
                original_width, original_height, width, height
            )
        else:
            target_width = width if width is not None else original_width
            target_height = height if height is not None else original_height

        # Apply resize based on mode
        if mode == 'fit':
            # Fit mode: resize to fit within bounds, preserving aspect ratio
            return image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        else:
            # Crop mode: resize and crop to fill exact dimensions
            return self._resize_and_crop(image, target_width, target_height)

    def _calculate_aspect_preserving_size(
        self,
        original_width: int,
        original_height: int,
        target_width: Optional[int],
        target_height: Optional[int]
    ) -> Tuple[int, int]:
        """
        Calculate new dimensions that preserve aspect ratio.

        Args:
            original_width: Original image width
            original_height: Original image height
            target_width: Desired width (optional)
            target_height: Desired height (optional)

        Returns:
            Tuple of (new_width, new_height)
        """
        aspect_ratio = original_width / original_height

        if target_width is not None and target_height is not None:
            # Both dimensions specified - fit within bounds
            width_ratio = target_width / original_width
            height_ratio = target_height / original_height
            ratio = min(width_ratio, height_ratio)
            return (int(original_width * ratio), int(original_height * ratio))

        elif target_width is not None:
            # Only width specified
            new_height = int(target_width / aspect_ratio)
            return (target_width, new_height)

        elif target_height is not None:
            # Only height specified
            new_width = int(target_height * aspect_ratio)
            return (new_width, target_height)

        return (original_width, original_height)

    def _resize_and_crop(
        self,
        image: Image.Image,
        target_width: int,
        target_height: int
    ) -> Image.Image:
        """
        Resize and crop image to exact dimensions.

        Args:
            image: Input PIL Image
            target_width: Target width
            target_height: Target height

        Returns:
            Resized and cropped PIL Image
        """
        original_width, original_height = image.size
        aspect_ratio = original_width / original_height
        target_aspect = target_width / target_height

        # Determine which dimension to match
        if aspect_ratio > target_aspect:
            # Image is wider - match height and crop width
            new_height = target_height
            new_width = int(new_height * aspect_ratio)
        else:
            # Image is taller - match width and crop height
            new_width = target_width
            new_height = int(new_width / aspect_ratio)

        # Resize image
        resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Calculate crop coordinates (center crop)
        left = (new_width - target_width) // 2
        top = (new_height - target_height) // 2
        right = left + target_width
        bottom = top + target_height

        # Crop to exact dimensions
        return resized.crop((left, top, right, bottom))

    def process_image(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
        preserve_aspect: bool = True,
        mode: Literal['fit', 'crop'] = 'fit',
        apply_eink: bool = False,
        output_format: Optional[str] = None,
        saturation: float = 2.5,
        contrast: float = 1.2,
        brightness: float = 1.1,
        sharpen: bool = True
    ) -> Optional[Tuple[BytesIO, str, str]]:
        """
        Process a random image with specified parameters.

        Processing pipeline:
        1. Select random image
        2. Load image
        3. Apply E-Ink color mapping (if requested)
        4. Resize (if requested)
        5. Convert to bytes

        Args:
            width: Target width (optional)
            height: Target height (optional)
            preserve_aspect: Whether to preserve aspect ratio
            mode: Resize mode - 'fit' or 'crop'
            apply_eink: Whether to apply Spectra-6 dithering
            output_format: Output format - 'jpeg', 'png', 'webp', 'bmp', 'gif' (optional, defaults to original)
            saturation: Saturation multiplier for E-Ink preprocessing (default 2.5)
            contrast: Contrast multiplier for E-Ink preprocessing (default 1.2)
            brightness: Brightness multiplier for E-Ink preprocessing (default 1.1)
            sharpen: Whether to apply sharpening for E-Ink preprocessing (default True)

        Returns:
            Tuple of (BytesIO buffer, content_type, filename) or None if no images
        """
        # Get random image
        image_path = self.get_random_image_path()
        if image_path is None:
            return None

        # Load image
        image = self.load_image(image_path)

        # Resize first if parameters provided
        # This ensures preprocessing and dithering happen at final resolution
        if width is not None or height is not None:
            image = self.resize_image(image, width, height, preserve_aspect, mode)

        # Apply E-Ink color mapping after resizing
        # Preprocessing (saturation/contrast) and dithering should be applied
        # at the final display resolution for optimal quality
        if apply_eink:
            image = apply_spectra_6(image, saturation, contrast, brightness, sharpen)

        # Determine output format
        if output_format:
            # User specified format - use it
            format_map = {
                'jpeg': ('JPEG', 'image/jpeg'),
                'png': ('PNG', 'image/png'),
                'webp': ('WEBP', 'image/webp'),
                'bmp': ('BMP', 'image/bmp'),
                'gif': ('GIF', 'image/gif')
            }
            pil_format, content_type = format_map.get(output_format.lower(), ('PNG', 'image/png'))
        else:
            # No format specified - use original file format
            original_format = image_path.suffix.lower()
            if original_format in {'.jpg', '.jpeg'}:
                pil_format = 'JPEG'
                content_type = 'image/jpeg'
            elif original_format == '.png':
                pil_format = 'PNG'
                content_type = 'image/png'
            elif original_format == '.bmp':
                pil_format = 'BMP'
                content_type = 'image/bmp'
            elif original_format == '.gif':
                pil_format = 'GIF'
                content_type = 'image/gif'
            elif original_format in {'.webp'}:
                pil_format = 'WEBP'
                content_type = 'image/webp'
            elif original_format in {'.tiff', '.tif'}:
                pil_format = 'TIFF'
                content_type = 'image/tiff'
            else:
                # Default to PNG for unknown formats
                pil_format = 'PNG'
                content_type = 'image/png'

        # Convert image to appropriate mode for the target format
        if pil_format == 'JPEG' and image.mode != 'RGB':
            # JPEG requires RGB mode (no transparency)
            image = image.convert('RGB')

        # Convert to bytes - Pillow handles format conversion automatically
        buffer = BytesIO()
        image.save(buffer, format=pil_format)
        buffer.seek(0)

        # Update filename extension to match the output format
        if output_format:
            # User specified format - change extension to match
            base_name = image_path.stem  # filename without extension
            extension_map = {
                'JPEG': '.jpg',
                'PNG': '.png',
                'WEBP': '.webp',
                'BMP': '.bmp',
                'GIF': '.gif'
            }
            new_extension = extension_map.get(pil_format, '.png')
            filename = f"{base_name}{new_extension}"
        else:
            # No format conversion - use original filename
            filename = image_path.name

        return (buffer, content_type, filename)
