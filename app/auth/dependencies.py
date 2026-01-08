"""
FastAPI dependencies for authentication and CSRF protection.

Provides dependency injection for route protection.
"""

import secrets
from typing import Optional
from fastapi import Request, HTTPException, status, Header

from app.auth.oidc import get_oidc_config
from app.utils.logging import logger

# CSRF token settings
CSRF_TOKEN_LENGTH = 32
CSRF_HEADER_NAME = "X-CSRF-Token"


class User:
    """Authenticated user information from OIDC."""

    def __init__(self, user_info: dict):
        self.sub = user_info.get("sub", "")
        self.email = user_info.get("email", "")
        self.name = user_info.get("name", user_info.get("preferred_username", ""))
        self.raw = user_info

    def __repr__(self):
        return f"User(email={self.email!r}, name={self.name!r})"


async def get_current_user(request: Request) -> User:
    """
    Dependency that requires authentication.

    Raises HTTPException 401 if user is not authenticated.
    """
    config = get_oidc_config()

    # If OIDC not configured, allow access (for development)
    if not config.enabled:
        logger.debug("OIDC not configured, allowing unauthenticated access")
        return User({"sub": "dev", "email": "dev@localhost", "name": "Developer"})

    # Check for user in session
    user_info = request.session.get("user")
    if not user_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return User(user_info)


async def get_optional_user(request: Request) -> Optional[User]:
    """
    Dependency that returns user if authenticated, None otherwise.

    Does not raise exception for unauthenticated users.
    """
    config = get_oidc_config()

    # If OIDC not configured, return dev user
    if not config.enabled:
        return User({"sub": "dev", "email": "dev@localhost", "name": "Developer"})

    # Check for user in session
    user_info = request.session.get("user")
    if not user_info:
        return None

    return User(user_info)


def get_csrf_token(request: Request) -> str:
    """
    Get or generate a CSRF token for the current session.

    The token is stored in the session and must be included in
    X-CSRF-Token header for all state-changing requests.
    """
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(CSRF_TOKEN_LENGTH)
    return request.session["csrf_token"]


async def verify_csrf_token(
    request: Request,
    x_csrf_token: Optional[str] = Header(None, alias=CSRF_HEADER_NAME)
) -> None:
    """
    Verify CSRF token from header matches session token (S5).

    Raises 403 Forbidden if tokens don't match.
    """
    config = get_oidc_config()

    # Skip CSRF check if OIDC not configured (development mode)
    if not config.enabled:
        return

    session_token = request.session.get("csrf_token")

    if not session_token or not x_csrf_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing"
        )

    if not secrets.compare_digest(session_token, x_csrf_token):
        logger.warning("CSRF token mismatch")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token invalid"
        )
