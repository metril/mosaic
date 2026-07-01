"""
Image Processing Service

Handles image selection, loading, resizing, and color mapping operations.
"""

import os
import re
import random
from pathlib import Path
from typing import Optional, Tuple, Literal, Dict, List, Any
from datetime import datetime
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS, GPSTAGS
from io import BytesIO

from app.utils.color_mapping import apply_spectra_6
from app.utils.path_security import validate_folder_paths
from app.exceptions import NoImagesFoundError
from app.utils.logging import logger


# Supported image formats
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tiff', '.tif'}

# Image directory (configurable via environment variable)
IMAGE_DIR = Path(os.environ.get('IMAGE_DIR', '/www/images'))

# Pre-compiled regex for CSS rgb()/rgba() color parsing (avoids recompilation on each call)
_RGB_PATTERN = re.compile(r'rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)')

# CSS named colors (basic + extended)
CSS_COLORS = {
    # Basic colors
    'black': (0, 0, 0),
    'white': (255, 255, 255),
    'red': (255, 0, 0),
    'green': (0, 128, 0),
    'blue': (0, 0, 255),
    'yellow': (255, 255, 0),
    'cyan': (0, 255, 255),
    'magenta': (255, 0, 255),
    'gray': (128, 128, 128),
    'grey': (128, 128, 128),
    # Extended colors
    'navy': (0, 0, 128),
    'maroon': (128, 0, 0),
    'olive': (128, 128, 0),
    'purple': (128, 0, 128),
    'teal': (0, 128, 128),
    'silver': (192, 192, 192),
    'lime': (0, 255, 0),
    'aqua': (0, 255, 255),
    'fuchsia': (255, 0, 255),
    'orange': (255, 165, 0),
    'pink': (255, 192, 203),
    'brown': (165, 42, 42),
    'coral': (255, 127, 80),
    'gold': (255, 215, 0),
    'indigo': (75, 0, 130),
    'ivory': (255, 255, 240),
    'khaki': (240, 230, 140),
    'lavender': (230, 230, 250),
    'beige': (245, 245, 220),
    'tan': (210, 180, 140),
    'crimson': (220, 20, 60),
    'salmon': (250, 128, 114),
    'tomato': (255, 99, 71),
    'skyblue': (135, 206, 235),
    'steelblue': (70, 130, 180),
    'slategray': (112, 128, 144),
    'slategrey': (112, 128, 144),
    'darkgray': (169, 169, 169),
    'darkgrey': (169, 169, 169),
    'lightgray': (211, 211, 211),
    'lightgrey': (211, 211, 211),
    'darkblue': (0, 0, 139),
    'darkgreen': (0, 100, 0),
    'darkred': (139, 0, 0),
}


class ImageService:
    """Service for managing and processing images."""

    def __init__(self, image_dir: Path = IMAGE_DIR):
        """
        Initialize the image service.

        Args:
            image_dir: Path to the directory containing images
        """
        self.image_dir = image_dir

    def get_image_files(
        self,
        folder_paths: Optional[list[str]] = None,
        recursive: bool = False
    ) -> list[Path]:
        """
        Get a list of supported image files, optionally filtered by folder paths.

        Args:
            folder_paths: Optional list of relative folder paths to search within.
                         If None, searches the base image directory.
                         Example: ["earth", "space/nebula"]
            recursive: If True, recursively search subfolders. If False, only
                      search the specified folder(s) non-recursively.

        Returns:
            List of Path objects for image files

        Raises:
            PathSecurityError: If any folder path fails security validation
        """
        image_files = []

        # Determine which directories to search
        if folder_paths:
            # Validate and resolve all folder paths
            search_dirs = validate_folder_paths(folder_paths, self.image_dir)
        else:
            # No folder specified - search base directory
            search_dirs = [self.image_dir]

        # Search each directory
        for search_dir in search_dirs:
            if not search_dir.exists():
                # Skip non-existent directories
                continue

            if recursive:
                # Recursive search using rglob
                for file_path in search_dir.rglob('*'):
                    # Skip hidden dirs/files (e.g. Immich pull staging ".<name>.tmp")
                    if any(part.startswith('.') for part in file_path.relative_to(search_dir).parts):
                        continue
                    if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_FORMATS:
                        # Additional security: verify file is not a symlink
                        if not file_path.is_symlink():
                            image_files.append(file_path)
            else:
                # Non-recursive search using iterdir
                for file_path in search_dir.iterdir():
                    if file_path.name.startswith('.'):
                        continue
                    if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_FORMATS:
                        # Additional security: verify file is not a symlink
                        if not file_path.is_symlink():
                            image_files.append(file_path)

        return image_files

    def get_random_image_path(
        self,
        folder_paths: Optional[list[str]] = None,
        recursive: bool = False
    ) -> Optional[Path]:
        """
        Get a random image file path, optionally filtered by folder paths.

        Args:
            folder_paths: Optional list of relative folder paths to search within
            recursive: If True, recursively search subfolders

        Returns:
            Path to a random image file, or None if no images found

        Raises:
            PathSecurityError: If any folder path fails security validation
        """
        image_files = self.get_image_files(folder_paths, recursive)
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

        Raises:
            ValueError: If image has invalid dimensions (0 width or height)
        """
        img = Image.open(image_path)

        # Apply the EXIF orientation tag to the pixels; without this, portrait
        # photos shot on phones render sideways once we re-encode (the tag is
        # lost on save but the pixels were never rotated).
        img = ImageOps.exif_transpose(img)

        # Validate dimensions to prevent division by zero in aspect ratio calculations
        if img.width == 0 or img.height == 0:
            raise ValueError(f"Invalid image dimensions: {img.width}x{img.height}")

        return img

    def _parse_css_color(self, color: str) -> Tuple[int, int, int]:
        """
        Parse CSS-style color string to RGB tuple.

        Supports:
        - Named colors: 'black', 'white', 'red', etc.
        - Hex 6-digit: '#ff0000' or 'ff0000'
        - Hex 3-digit: '#f00' or 'f00'
        - RGB: 'rgb(255,0,0)'
        - RGBA: 'rgba(255,0,0,128)' (alpha ignored)

        Returns (0, 0, 0) black on invalid input.
        """
        color = color.strip().lower()

        # Check named colors first
        if color in CSS_COLORS:
            return CSS_COLORS[color]

        # Strip leading # if present
        if color.startswith('#'):
            color = color[1:]

        # Try 6-digit hex
        if len(color) == 6 and all(c in '0123456789abcdef' for c in color):
            return (
                int(color[0:2], 16),
                int(color[2:4], 16),
                int(color[4:6], 16)
            )

        # Try 3-digit hex (expand to 6-digit)
        if len(color) == 3 and all(c in '0123456789abcdef' for c in color):
            return (
                int(color[0] * 2, 16),
                int(color[1] * 2, 16),
                int(color[2] * 2, 16)
            )

        # Try rgb() or rgba() function (uses pre-compiled pattern)
        rgb_match = _RGB_PATTERN.match(color)
        if rgb_match:
            r = min(255, max(0, int(rgb_match.group(1))))
            g = min(255, max(0, int(rgb_match.group(2))))
            b = min(255, max(0, int(rgb_match.group(3))))
            return (r, g, b)

        # Default to black on invalid input
        return (0, 0, 0)

    def resize_image(
        self,
        image: Image.Image,
        width: Optional[int] = None,
        height: Optional[int] = None,
        preserve_aspect: bool = True,
        mode: Literal['fit', 'crop'] = 'fit',
        pad_color: str = "black"
    ) -> Image.Image:
        """
        Resize an image with specified parameters.

        Args:
            image: Input PIL Image
            width: Target width (optional)
            height: Target height (optional)
            preserve_aspect: Whether to preserve aspect ratio
            mode: Resize mode - 'fit' (letterbox) or 'crop' (fill)
            pad_color: Hex color for letterbox padding (default: black)

        Returns:
            Resized PIL Image
        """
        # If no dimensions specified, return original
        if width is None and height is None:
            return image

        original_width, original_height = image.size

        if mode == 'fit':
            if preserve_aspect:
                # Calculate size that fits within bounds while preserving aspect
                target_width, target_height = self._calculate_aspect_preserving_size(
                    original_width, original_height, width, height
                )
                resized = image.resize((target_width, target_height), Image.Resampling.LANCZOS)

                # If both dimensions specified, add letterbox padding
                if width is not None and height is not None:
                    # Parse padding color
                    rgb = self._parse_css_color(pad_color)

                    # Create canvas matching image mode
                    if image.mode == 'RGBA':
                        canvas = Image.new('RGBA', (width, height), (*rgb, 255))
                    elif image.mode == 'LA':
                        # Grayscale with alpha - use luminance of RGB
                        luminance = int(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2])
                        canvas = Image.new('LA', (width, height), (luminance, 255))
                    elif image.mode == 'L':
                        # Grayscale - use luminance of RGB
                        luminance = int(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2])
                        canvas = Image.new('L', (width, height), luminance)
                    else:
                        # RGB or other modes
                        canvas = Image.new('RGB', (width, height), rgb)

                    # Center the resized image on the canvas
                    x_offset = (width - target_width) // 2
                    y_offset = (height - target_height) // 2
                    canvas.paste(resized, (x_offset, y_offset))
                    return canvas

                return resized
            else:
                # Stretch to exact dimensions (no aspect preservation)
                target_width = width if width is not None else original_width
                target_height = height if height is not None else original_height
                return image.resize((target_width, target_height), Image.Resampling.LANCZOS)

        else:  # mode == 'crop'
            if preserve_aspect:
                # Resize and crop to fill exact dimensions
                return self._resize_and_crop(
                    image,
                    width if width is not None else original_width,
                    height if height is not None else original_height
                )
            else:
                # Same as fit when not preserving aspect
                target_width = width if width is not None else original_width
                target_height = height if height is not None else original_height
                return image.resize((target_width, target_height), Image.Resampling.LANCZOS)

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
        pad_color: str = "black",
        apply_eink: bool = False,
        output_format: Optional[str] = None,
        saturation: float = 2.5,
        contrast: float = 1.2,
        brightness: float = 1.1,
        sharpen: bool = True,
        folder_paths: Optional[list[str]] = None,
        recursive: bool = False
    ) -> Optional[Tuple[BytesIO, str, str, str, float]]:
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
            pad_color: Hex color for letterbox padding (default: black)
            apply_eink: Whether to apply Spectra-6 dithering
            output_format: Output format - 'jpeg', 'png', 'webp', 'bmp', 'gif' (optional, defaults to original)
            saturation: Saturation multiplier for E-Ink preprocessing (default 2.5)
            contrast: Contrast multiplier for E-Ink preprocessing (default 1.2)
            brightness: Brightness multiplier for E-Ink preprocessing (default 1.1)
            sharpen: Whether to apply sharpening for E-Ink preprocessing (default True)
            folder_paths: Optional list of relative folder paths to search within
            recursive: If True, recursively search subfolders

        Returns:
            Tuple of (BytesIO buffer, content_type, filename, relative_path, mtime)
            or None if no images found.
            - relative_path: can be used to re-request the same image via /api/image/{path}
            - mtime: file modification time (Unix timestamp)

        Raises:
            PathSecurityError: If any folder path fails security validation
        """
        # Get random image from specified folders
        image_path = self.get_random_image_path(folder_paths, recursive)
        if image_path is None:
            return None

        # Load image
        image = self.load_image(image_path)

        # Resize first if parameters provided
        # This ensures preprocessing and dithering happen at final resolution
        if width is not None or height is not None:
            image = self.resize_image(image, width, height, preserve_aspect, mode, pad_color)

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

        # Calculate relative path from image directory for re-request capability
        relative_path = str(image_path.relative_to(self.image_dir))

        # Get file modification time for Last-Modified header
        mtime = image_path.stat().st_mtime

        return (buffer, content_type, filename, relative_path, mtime)

    def _extract_exif(self, img: Image.Image) -> Dict[str, Any]:
        """
        Extract and parse EXIF data from a PIL Image.

        Args:
            img: PIL Image object

        Returns:
            Dict with parsed EXIF data
        """
        exif_data = {}

        try:
            exif = img.getexif()
            if not exif:
                return exif_data

            for tag_id, value in exif.items():
                # Get the tag name
                tag = TAGS.get(tag_id, tag_id)

                # Convert bytes to string for readability
                if isinstance(value, bytes):
                    try:
                        value = value.decode('utf-8', errors='ignore')
                    except (UnicodeDecodeError, AttributeError, TypeError):
                        value = str(value)

                # Handle GPS info separately
                if tag == 'GPSInfo':
                    gps_data = {}
                    for gps_tag_id, gps_value in value.items():
                        gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                        gps_data[gps_tag] = gps_value
                    exif_data['GPSInfo'] = gps_data
                else:
                    # Store common/useful EXIF tags
                    exif_data[tag] = value

            # Parse specific useful fields
            parsed = {}

            # Camera info
            if 'Make' in exif_data:
                parsed['camera_make'] = exif_data['Make']
            if 'Model' in exif_data:
                parsed['camera_model'] = exif_data['Model']

            # Date/time
            if 'DateTime' in exif_data:
                parsed['date_time'] = exif_data['DateTime']
            if 'DateTimeOriginal' in exif_data:
                parsed['date_time_original'] = exif_data['DateTimeOriginal']

            # Camera settings
            if 'ExposureTime' in exif_data:
                parsed['exposure_time'] = str(exif_data['ExposureTime'])
            if 'FNumber' in exif_data:
                parsed['f_number'] = str(exif_data['FNumber'])
            if 'ISOSpeedRatings' in exif_data:
                parsed['iso'] = exif_data['ISOSpeedRatings']
            if 'FocalLength' in exif_data:
                parsed['focal_length'] = str(exif_data['FocalLength'])

            # Orientation
            if 'Orientation' in exif_data:
                parsed['orientation'] = exif_data['Orientation']

            # GPS
            if 'GPSInfo' in exif_data:
                parsed['gps'] = exif_data['GPSInfo']

            # Software
            if 'Software' in exif_data:
                parsed['software'] = exif_data['Software']

            # Add raw EXIF for completeness
            parsed['raw'] = {k: str(v) if not isinstance(v, dict) else v
                           for k, v in exif_data.items()}

            return parsed

        except Exception as e:
            logger.warning(f"Failed to extract EXIF data: {e}")
            return {"error": f"Failed to extract EXIF: {str(e)}"}

    def get_folder_list(self) -> List[str]:
        """
        Get list of top-level folder names in the image directory.

        Returns:
            List of folder names (not full paths)
        """
        folders = []
        try:
            for item in self.image_dir.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    folders.append(item.name)
            folders.sort()
        except Exception as e:
            logger.error(f"Failed to list folders: {e}")
        return folders

    def get_image_metadata(self, file_path: Path, include_exif: bool = False) -> Dict[str, Any]:
        """
        Extract metadata from an image file.

        Args:
            file_path: Path to the image file
            include_exif: Whether to include EXIF data (default False for performance)

        Returns:
            Dict with file metadata (name, size, dimensions, modified time, optionally EXIF)
        """
        try:
            # Get file stats
            stats = file_path.stat()
            file_size = stats.st_size
            modified_time = datetime.fromtimestamp(stats.st_mtime)

            # Format file size
            if file_size < 1024:
                size_formatted = f"{file_size} B"
            elif file_size < 1024 * 1024:
                size_formatted = f"{file_size / 1024:.1f} KB"
            else:
                size_formatted = f"{file_size / (1024 * 1024):.1f} MB"

            # Get image dimensions efficiently (only reads header)
            exif_data = None
            try:
                with Image.open(file_path) as img:
                    dimensions = f"{img.width}x{img.height}"
                    width, height = img.width, img.height
                    format_name = img.format
                    mode = img.mode

                    # Extract EXIF data if requested
                    if include_exif:
                        exif_data = self._extract_exif(img)
            except Exception as e:
                logger.warning(f"Failed to read image dimensions for {file_path}: {e}")
                dimensions = "Unknown"
                width, height = None, None
                format_name = None
                mode = None

            # Get relative path from image directory
            try:
                relative_path = file_path.relative_to(self.image_dir)
            except ValueError:
                relative_path = file_path.name

            metadata = {
                "name": file_path.name,
                "path": str(relative_path),
                "size": file_size,
                "size_formatted": size_formatted,
                "dimensions": dimensions,
                "width": width,
                "height": height,
                "format": format_name,
                "mode": mode,
                "modified": modified_time.isoformat(),
                "type": "file"
            }

            if include_exif and exif_data:
                metadata["exif"] = exif_data

            return metadata
        except Exception as e:
            # Return basic info if metadata extraction fails
            logger.warning(f"Failed to extract metadata for {file_path}: {e}")
            return {
                "name": file_path.name,
                "path": str(file_path.name),
                "size": 0,
                "size_formatted": "Unknown",
                "dimensions": "Unknown",
                "width": None,
                "height": None,
                "modified": None,
                "type": "file",
                "error": str(e)
            }

    def _calculate_folder_stats(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate statistics for a folder based on its image files.

        Args:
            files: List of file metadata dictionaries

        Returns:
            Dict with folder statistics (formats, orientations, size ranges)
        """
        if not files:
            return {}

        stats = {
            "formats": {},
            "orientations": {},
            "total_size": 0,
            "size_range": {"min": float('inf'), "max": 0},
            "dimension_range": {
                "width": {"min": float('inf'), "max": 0},
                "height": {"min": float('inf'), "max": 0}
            }
        }

        for file_meta in files:
            # Count formats
            if file_meta.get("format"):
                fmt = file_meta["format"]
                stats["formats"][fmt] = stats["formats"].get(fmt, 0) + 1

            # Determine orientation
            width = file_meta.get("width")
            height = file_meta.get("height")
            if width and height:
                if width > height:
                    orientation = "landscape"
                elif height > width:
                    orientation = "portrait"
                else:
                    orientation = "square"
                stats["orientations"][orientation] = stats["orientations"].get(orientation, 0) + 1

                # Track dimension ranges
                stats["dimension_range"]["width"]["min"] = min(stats["dimension_range"]["width"]["min"], width)
                stats["dimension_range"]["width"]["max"] = max(stats["dimension_range"]["width"]["max"], width)
                stats["dimension_range"]["height"]["min"] = min(stats["dimension_range"]["height"]["min"], height)
                stats["dimension_range"]["height"]["max"] = max(stats["dimension_range"]["height"]["max"], height)

            # Track size
            size = file_meta.get("size", 0)
            if size:
                stats["total_size"] += size
                stats["size_range"]["min"] = min(stats["size_range"]["min"], size)
                stats["size_range"]["max"] = max(stats["size_range"]["max"], size)

        # Format total size
        total_size = stats["total_size"]
        if total_size < 1024:
            stats["total_size_formatted"] = f"{total_size} B"
        elif total_size < 1024 * 1024:
            stats["total_size_formatted"] = f"{total_size / 1024:.1f} KB"
        elif total_size < 1024 * 1024 * 1024:
            stats["total_size_formatted"] = f"{total_size / (1024 * 1024):.1f} MB"
        else:
            stats["total_size_formatted"] = f"{total_size / (1024 * 1024 * 1024):.2f} GB"

        # Clean up infinite values
        if stats["size_range"]["min"] == float('inf'):
            stats["size_range"]["min"] = 0
        if stats["dimension_range"]["width"]["min"] == float('inf'):
            stats["dimension_range"]["width"]["min"] = 0
        if stats["dimension_range"]["height"]["min"] == float('inf'):
            stats["dimension_range"]["height"]["min"] = 0

        return stats

    @staticmethod
    def _format_size(file_size: int) -> str:
        """Human-readable byte size (shared by the fast scan)."""
        if file_size < 1024:
            return f"{file_size} B"
        if file_size < 1024 * 1024:
            return f"{file_size / 1024:.1f} KB"
        if file_size < 1024 * 1024 * 1024:
            return f"{file_size / (1024 * 1024):.1f} MB"
        return f"{file_size / (1024 * 1024 * 1024):.2f} GB"

    def build_browse_index(self) -> Dict[str, Any]:
        """
        Fast, metadata-free scan of the image directory.

        Unlike build_directory_tree(), this never opens image files (no
        dimensions/EXIF) — it only walks directories and stats files, so it
        stays fast on large libraries. Produces, in a single pass:

          - a folders-only tree (each folder has file_count/folder_count and
            child *folders* only), for the sidebar, and
          - a flat, path-sorted list of every image file (stat-only metadata),
            for paginated/filtered grid queries.

        Returns:
            {root, total_folders, total_files, tree, files}
        """
        flat_files: List[Dict[str, Any]] = []

        def scan(directory: Path, relative_path: str) -> Dict[str, Any]:
            folder = {
                "type": "folder",
                "name": directory.name if directory.name else "images",
                "path": relative_path,
                "children": [],
                "file_count": 0,
                "folder_count": 0,
            }
            try:
                if not directory.exists() or directory.is_symlink():
                    return folder
                entries = sorted(
                    directory.iterdir(),
                    key=lambda x: (not x.is_dir(), x.name.lower()),
                )
                for item in entries:
                    if item.is_symlink() or item.name.startswith('.'):
                        continue
                    if item.is_dir():
                        child_rel = f"{relative_path}/{item.name}" if relative_path else item.name
                        child = scan(item, child_rel)
                        folder["children"].append(child)
                        folder["folder_count"] += 1
                        folder["file_count"] += child["file_count"]
                    elif item.is_file() and item.suffix.lower() in SUPPORTED_FORMATS:
                        try:
                            st = item.stat()
                            size = st.st_size
                            modified = datetime.fromtimestamp(st.st_mtime).isoformat()
                        except OSError:
                            size, modified = 0, None
                        rel = f"{relative_path}/{item.name}" if relative_path else item.name
                        flat_files.append({
                            "type": "file",
                            "name": item.name,
                            "path": rel,
                            "size": size,
                            "size_formatted": self._format_size(size),
                            "dimensions": "Unknown",
                            "width": None,
                            "height": None,
                            "format": None,
                            "mode": None,
                            "modified": modified,
                        })
                        folder["file_count"] += 1
            except PermissionError:
                logger.debug(f"Permission denied reading directory: {directory}")
            except Exception as e:
                logger.warning(f"Error scanning directory {directory}: {e}")
                folder["error"] = str(e)
            return folder

        tree = scan(self.image_dir, "")
        flat_files.sort(key=lambda f: f["path"].lower())

        def count_folders(node: Dict[str, Any]) -> int:
            return sum(1 + count_folders(c) for c in node["children"] if c["type"] == "folder")

        return {
            "root": str(self.image_dir),
            "total_folders": count_folders(tree),
            "total_files": len(flat_files),
            "tree": tree,
            "files": flat_files,
        }

    def build_directory_tree(self) -> Dict[str, Any]:
        """
        Build a complete recursive directory tree of all folders and image files with statistics.

        Returns:
            Dict containing the complete directory tree structure with metadata and statistics
        """
        def scan_directory(directory: Path, relative_path: str = "") -> Dict[str, Any]:
            """
            Recursively scan a directory and build tree structure.

            Args:
                directory: Absolute path to directory to scan
                relative_path: Relative path from image root

            Returns:
                Dict representing folder with children
            """
            folder_data = {
                "type": "folder",
                "name": directory.name if directory.name else "images",
                "path": relative_path,
                "children": [],
                "file_count": 0,
                "folder_count": 0
            }

            try:
                # Skip if directory doesn't exist or is a symlink
                if not directory.exists() or directory.is_symlink():
                    return folder_data

                # Get all items in directory
                items = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))

                for item in items:
                    # Skip symlinks
                    if item.is_symlink():
                        continue

                    # Skip hidden files/folders
                    if item.name.startswith('.'):
                        continue

                    if item.is_dir():
                        # Recursively scan subdirectory
                        child_relative_path = f"{relative_path}/{item.name}" if relative_path else item.name
                        child_folder = scan_directory(item, child_relative_path)
                        folder_data["children"].append(child_folder)
                        folder_data["folder_count"] += 1
                        # Propagate counts up from children
                        folder_data["file_count"] += child_folder["file_count"]

                    elif item.is_file() and item.suffix.lower() in SUPPORTED_FORMATS:
                        # Add image file with metadata
                        file_metadata = self.get_image_metadata(item)
                        folder_data["children"].append(file_metadata)
                        folder_data["file_count"] += 1

            except PermissionError:
                logger.debug(f"Permission denied reading directory: {directory}")
            except Exception as e:
                logger.warning(f"Error scanning directory {directory}: {e}")
                folder_data["error"] = str(e)

            # Calculate folder statistics recursively (include all descendant files)
            all_files = self._collect_all_files(folder_data)
            if all_files:
                folder_data["stats"] = self._calculate_folder_stats(all_files)

            return folder_data

        def _collect_all_files(node: Dict[str, Any]) -> List[Dict[str, Any]]:
            """
            Recursively collect all file metadata from a folder node and its children.

            Args:
                node: Folder node with children

            Returns:
                List of all file metadata dictionaries
            """
            files = []
            for child in node.get("children", []):
                if child.get("type") == "file":
                    files.append(child)
                elif child.get("type") == "folder":
                    # Recursively collect files from subfolders
                    files.extend(_collect_all_files(child))
            return files

        # Make _collect_all_files accessible within scan_directory
        self._collect_all_files = _collect_all_files

        # Build the complete tree starting from image root
        tree = scan_directory(self.image_dir, "")

        # Count totals
        def count_totals(node: Dict[str, Any]) -> Tuple[int, int]:
            """Recursively count total folders and files."""
            if node["type"] == "file":
                return (0, 1)

            total_folders = 0
            total_files = 0

            for child in node.get("children", []):
                child_folders, child_files = count_totals(child)
                total_folders += child_folders
                total_files += child_files

            if node["type"] == "folder":
                total_folders += 1

            return (total_folders, total_files)

        total_folders, total_files = count_totals(tree)

        return {
            "root": str(self.image_dir),
            "total_folders": total_folders - 1,  # Exclude root from count
            "total_files": total_files,
            "tree": tree
        }
