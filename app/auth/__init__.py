"""
Authentication module for Mosaic application.

Provides OIDC authentication for admin endpoints.
"""

from app.auth.dependencies import get_current_user, get_optional_user
from app.auth.oidc import oidc_client, get_oidc_config

__all__ = [
    "get_current_user",
    "get_optional_user",
    "oidc_client",
    "get_oidc_config",
]
