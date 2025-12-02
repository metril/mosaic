"""
Custom exception classes for the screensaver application.

These exceptions are used throughout the application and mapped to appropriate
HTTP status codes in the API layer.
"""


class ScreensaverException(Exception):
    """Base exception for all screensaver application errors."""
    pass


class PathSecurityError(ScreensaverException):
    """Base exception for path security violations (maps to 400 Bad Request)."""
    pass


class PathTraversalError(PathSecurityError):
    """Raised when path traversal attempt is detected (maps to 400 Bad Request)."""
    pass


class InvalidPathError(PathSecurityError):
    """Raised when path contains invalid characters or format (maps to 400 Bad Request)."""
    pass


class SymlinkError(PathSecurityError):
    """Raised when path contains or points to a symlink (maps to 400 Bad Request)."""
    pass


class NoImagesFoundError(ScreensaverException):
    """Raised when no images are found in the specified location (maps to 404 Not Found)."""
    pass
