"""
Authentication routes for OIDC login/logout flow.

Endpoints:
- GET /auth/login: Redirect to OIDC provider
- GET /auth/callback: Handle OIDC callback, create session
- GET /auth/logout: Clear session and redirect
- GET /auth/me: Get current user info (for debugging)
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse

from app.auth.oidc import get_oidc_client, get_oidc_config
from app.auth.dependencies import get_current_user, User
from app.utils.logging import logger

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/login")
async def login(request: Request, redirect: str = "/sync/admin"):
    """
    Initiate OIDC login flow.

    Redirects user to OIDC provider for authentication.
    After successful auth, user is redirected back to /auth/callback.
    """
    config = get_oidc_config()

    if not config.enabled:
        # OIDC not configured - redirect directly (dev mode)
        logger.warning("OIDC not configured, skipping authentication")
        return RedirectResponse(url=redirect)

    client = get_oidc_client()
    if not client:
        raise HTTPException(status_code=500, detail="OIDC client not initialized")

    # Store the intended destination in session
    request.session["auth_redirect"] = redirect

    # Determine redirect URI
    redirect_uri = config.redirect_uri
    if not redirect_uri:
        # Auto-detect from request
        redirect_uri = str(request.url_for("auth_callback"))

    return await client.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def auth_callback(request: Request):
    """
    Handle OIDC callback after user authenticates.

    Exchanges authorization code for tokens, validates ID token,
    and creates user session.
    """
    config = get_oidc_config()

    if not config.enabled:
        return RedirectResponse(url="/sync/admin")

    client = get_oidc_client()
    if not client:
        raise HTTPException(status_code=500, detail="OIDC client not initialized")

    try:
        # Exchange code for tokens
        token = await client.authorize_access_token(request)

        # Get user info from ID token
        user_info = token.get("userinfo")
        if not user_info:
            # Fall back to userinfo endpoint
            user_info = await client.userinfo(token=token)

        if not user_info:
            raise HTTPException(status_code=400, detail="Failed to get user info")

        # Store user in session
        request.session["user"] = dict(user_info)
        logger.info(f"User authenticated: {user_info.get('email', user_info.get('sub'))}")

        # Redirect to original destination
        redirect = request.session.pop("auth_redirect", "/sync/admin")
        return RedirectResponse(url=redirect)

    except Exception as e:
        logger.error(f"OIDC callback error: {e}")
        raise HTTPException(status_code=400, detail=f"Authentication failed: {e}")


@router.get("/logout")
async def logout(request: Request, redirect: str = "/"):
    """
    Log out user by clearing session.

    Optionally, can also redirect to OIDC provider's logout endpoint
    for single logout (SLO).
    """
    # Clear session
    request.session.clear()
    logger.info("User logged out")

    # Redirect to home or specified URL
    return RedirectResponse(url=redirect)


@router.get("/me")
async def get_me(request: Request):
    """
    Get current user info (for debugging/verification).

    Returns user info if authenticated, 401 otherwise.
    """
    user = await get_current_user(request)
    return {
        "sub": user.sub,
        "email": user.email,
        "name": user.name,
    }
