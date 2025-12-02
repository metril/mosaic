"""
Security utilities for validating and sanitizing folder paths.

Prevents path traversal attacks, symlink exploits, and other security vulnerabilities
when handling user-provided folder paths.
"""

import re
from pathlib import Path
from typing import Optional


# Security constants
MAX_PATH_DEPTH = 10  # Maximum number of directory levels
MAX_PATH_LENGTH = 255  # Maximum path length in characters
ALLOWED_PATH_PATTERN = re.compile(r'^[a-zA-Z0-9_\-/]*$')  # Whitelist: alphanumeric, dash, underscore, slash


class PathSecurityError(Exception):
    """Base exception for path security violations."""
    pass


class PathTraversalError(PathSecurityError):
    """Raised when path traversal attempt is detected."""
    pass


class InvalidPathError(PathSecurityError):
    """Raised when path contains invalid characters or format."""
    pass


class SymlinkError(PathSecurityError):
    """Raised when path contains or points to a symlink."""
    pass


def validate_folder_path(folder_path: str, base_dir: Path) -> Path:
    """
    Validate and sanitize a user-provided folder path.

    Performs comprehensive security checks:
    - Blocks path traversal (.. components)
    - Rejects absolute paths
    - Rejects null bytes
    - Validates character whitelist
    - Enforces maximum path depth and length
    - Rejects hidden folders (starting with .)
    - Rejects symlinks
    - Ensures resolved path stays within base directory

    Args:
        folder_path: User-provided relative folder path (e.g., "earth/folder1")
        base_dir: Base directory that paths must stay within

    Returns:
        Resolved absolute Path object within base_dir

    Raises:
        PathTraversalError: If path attempts to escape base directory
        InvalidPathError: If path contains invalid characters or format
        SymlinkError: If path contains or points to a symlink
    """
    # Handle empty path (root)
    if not folder_path or folder_path == '/':
        return base_dir

    # Strip leading/trailing slashes and whitespace
    folder_path = folder_path.strip().strip('/')

    # Check for null bytes
    if '\0' in folder_path:
        raise InvalidPathError("Path contains null bytes")

    # Check path length
    if len(folder_path) > MAX_PATH_LENGTH:
        raise InvalidPathError(f"Path exceeds maximum length of {MAX_PATH_LENGTH} characters")

    # Check for absolute path
    if folder_path.startswith('/') or (len(folder_path) > 1 and folder_path[1] == ':'):
        raise PathTraversalError("Absolute paths are not allowed")

    # Check for path traversal attempts
    if '..' in folder_path:
        raise PathTraversalError("Parent directory references (..) are not allowed")

    # Validate character whitelist
    if not ALLOWED_PATH_PATTERN.match(folder_path):
        raise InvalidPathError("Path contains invalid characters (only alphanumeric, dash, underscore, and slash allowed)")

    # Check for hidden folders (starting with .)
    path_parts = folder_path.split('/')
    for part in path_parts:
        if part.startswith('.'):
            raise InvalidPathError("Hidden folders (starting with .) are not allowed")

    # Check path depth
    if len(path_parts) > MAX_PATH_DEPTH:
        raise InvalidPathError(f"Path depth exceeds maximum of {MAX_PATH_DEPTH} levels")

    # Construct and resolve the full path
    full_path = base_dir / folder_path

    # Resolve to absolute path (follows symlinks during resolution, but we'll check after)
    try:
        resolved_path = full_path.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise InvalidPathError(f"Cannot resolve path: {e}")

    # Verify the resolved path is still within base_dir
    try:
        resolved_base = base_dir.resolve(strict=False)
        resolved_path.relative_to(resolved_base)
    except ValueError:
        raise PathTraversalError("Path escapes base directory")

    # Check for symlinks in the path
    # We need to check each component of the path for symlinks
    current_path = base_dir
    for part in path_parts:
        current_path = current_path / part
        if current_path.exists() and current_path.is_symlink():
            raise SymlinkError("Symlinks are not allowed in folder paths")

    return resolved_path


def validate_folder_paths(folder_paths: list[str], base_dir: Path) -> list[Path]:
    """
    Validate multiple folder paths.

    Args:
        folder_paths: List of user-provided folder paths
        base_dir: Base directory that paths must stay within

    Returns:
        List of validated resolved Path objects

    Raises:
        PathSecurityError: If any path fails validation
    """
    validated_paths = []
    for folder_path in folder_paths:
        validated_path = validate_folder_path(folder_path, base_dir)
        validated_paths.append(validated_path)
    return validated_paths
