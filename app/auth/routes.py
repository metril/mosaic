"""
Authentication routes for OIDC login/logout flow.

Endpoints:
- GET /auth/login: Show login page or redirect to OIDC provider
- GET /auth/callback: Handle OIDC callback, create session
- GET /auth/logout: Clear session and redirect
- GET /auth/me: Get current user info (for debugging)
- GET /auth/debug: Debug endpoint for OIDC configuration
"""

from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.oidc import get_oidc_client, get_oidc_config
from app.auth.dependencies import get_current_user, User
from app.utils.logging import logger

router = APIRouter(prefix="/auth", tags=["authentication"])

# Templates for login page
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "templates")


@router.get("/login")
async def login(
    request: Request,
    redirect: str = "/admin",
    action: Optional[str] = None,
    error: Optional[str] = None,
):
    """
    Show login page or initiate OIDC login flow.

    - Default: Shows a login page with "Sign in with [Provider]" button
    - With action=redirect: Redirects to OIDC provider for authentication
    - After successful auth, user is redirected back to /auth/callback
    """
    config = get_oidc_config()

    if not config.enabled:
        # OIDC not configured - redirect directly (dev mode)
        logger.warning("OIDC not configured, skipping authentication")
        return RedirectResponse(url=redirect)

    # If action=redirect, initiate OIDC flow
    if action == "redirect":
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

    # Otherwise, show login page
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "redirect": redirect,
            "provider_name": config.provider_name,
            "error": error,
        },
    )


@router.get("/callback")
async def auth_callback(request: Request):
    """
    Handle OIDC callback after user authenticates.

    Exchanges authorization code for tokens, validates ID token,
    and creates user session.
    """
    config = get_oidc_config()

    if not config.enabled:
        return RedirectResponse(url="/admin")

    client = get_oidc_client()
    if not client:
        raise HTTPException(status_code=500, detail="OIDC client not initialized")

    # Get intended redirect destination
    redirect_dest = request.session.pop("auth_redirect", "/admin")

    try:
        # Exchange code for tokens
        token = await client.authorize_access_token(request)

        # Get user info from ID token
        user_info = token.get("userinfo")
        if not user_info:
            # Fall back to userinfo endpoint
            user_info = await client.userinfo(token=token)

        if not user_info:
            logger.error("Failed to get user info from OIDC token")
            error_params = urlencode({"error": "Failed to get user info", "redirect": redirect_dest})
            return RedirectResponse(url=f"/auth/login?{error_params}")

        # Store user in session
        request.session["user"] = dict(user_info)
        logger.info(f"User authenticated: {user_info.get('email', user_info.get('sub'))}")

        # Redirect to original destination
        return RedirectResponse(url=redirect_dest)

    except Exception as e:
        logger.error(f"OIDC callback error: {e}")
        error_params = urlencode({"error": f"Authentication failed: {e}", "redirect": redirect_dest})
        return RedirectResponse(url=f"/auth/login?{error_params}")


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


@router.get("/debug")
async def auth_debug(request: Request):
    """
    Debug endpoint to check authentication state.

    Returns OIDC configuration status and session information
    to help diagnose authentication issues.
    """
    config = get_oidc_config()

    # Safely truncate issuer for display
    issuer_display = None
    if config.issuer:
        issuer_display = config.issuer[:50] + "..." if len(config.issuer) > 50 else config.issuer

    return {
        "oidc_enabled": config.enabled,
        "oidc_issuer": issuer_display,
        "oidc_client_id_set": bool(config.client_id),
        "oidc_client_secret_set": bool(config.client_secret),
        "oidc_redirect_uri": config.redirect_uri or "(auto-detect)",
        "session_has_user": "user" in request.session,
        "session_keys": list(request.session.keys()),
        "provider_name": config.provider_name,
    }
