"""
FastAPI dependencies for authentication.

Provides dependency injection for route protection.
"""

from typing import Optional
from fastapi import Request, HTTPException, status

from app.auth.oidc import get_oidc_config
from app.utils.logging import logger


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
