"""
OIDC Client Configuration for OpenID Connect authentication.

Environment variables:
- OIDC_ISSUER: OIDC issuer URL (e.g., https://keycloak.example.com/realms/myrealm)
- OIDC_CLIENT_ID: Client ID from OIDC provider
- OIDC_CLIENT_SECRET: Client secret
- OIDC_REDIRECT_URI: Callback URL (e.g., https://app.example.com/auth/callback)
- SESSION_SECRET: Secret for session cookie encryption (generate with: openssl rand -hex 32)
"""

import os
from dataclasses import dataclass
from typing import Optional
from authlib.integrations.starlette_client import OAuth
from authlib.integrations.base_client import OAuthError

from app.utils.logging import logger


@dataclass
class OIDCConfig:
    """OIDC configuration from environment variables."""
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    session_secret: str
    provider_name: str
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "OIDCConfig":
        """Load configuration from environment variables."""
        issuer = os.environ.get("OIDC_ISSUER", "")
        client_id = os.environ.get("OIDC_CLIENT_ID", "")
        client_secret = os.environ.get("OIDC_CLIENT_SECRET", "")
        redirect_uri = os.environ.get("OIDC_REDIRECT_URI", "")
        session_secret = os.environ.get("SESSION_SECRET", "")
        provider_name = os.environ.get("OIDC_PROVIDER_NAME", "SSO")

        # Check if OIDC is configured
        enabled = bool(issuer and client_id and client_secret)

        if not enabled:
            logger.warning(
                "OIDC authentication not configured. "
                "Set OIDC_ISSUER, OIDC_CLIENT_ID, and OIDC_CLIENT_SECRET to enable."
            )

        if enabled and not session_secret:
            # A known/static secret means anyone can forge session cookies and
            # impersonate any user — refuse to start rather than run insecurely.
            raise RuntimeError(
                "SESSION_SECRET must be set when OIDC is enabled. "
                "Generate one with: openssl rand -hex 32"
            )

        return cls(
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            session_secret=session_secret,
            provider_name=provider_name,
            enabled=enabled,
        )


# Global OIDC configuration
_oidc_config: Optional[OIDCConfig] = None


def get_oidc_config() -> OIDCConfig:
    """Get OIDC configuration (lazy initialization)."""
    global _oidc_config
    if _oidc_config is None:
        _oidc_config = OIDCConfig.from_env()
    return _oidc_config


# OAuth client instance
oauth = OAuth()


def get_oidc_client():
    """Get the OIDC client, initializing if needed."""
    config = get_oidc_config()

    if not config.enabled:
        return None

    # Register the OIDC provider if not already done
    if "oidc" not in oauth._clients:
        oauth.register(
            name="oidc",
            client_id=config.client_id,
            client_secret=config.client_secret,
            server_metadata_url=f"{config.issuer}/.well-known/openid-configuration",
            client_kwargs={
                "scope": "openid profile email",
            },
        )

    return oauth.oidc


# Convenience export
oidc_client = get_oidc_client
