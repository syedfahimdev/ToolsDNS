"""
auth.py — Simple API key authentication for ToolDNS.

Provides a FastAPI dependency that validates Bearer tokens
against the configured API key. In dev mode (default key),
auth is effectively open.

Usage:
    from tooldns.auth import require_api_key
    
    @app.get("/v1/tools", dependencies=[Depends(require_api_key)])
    def list_tools(): ...
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from tooldns.config import settings

security = HTTPBearer(auto_error=False)


async def require_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """
    FastAPI dependency that validates the Bearer token.

    Compares the provided token against TOOLDNS_API_KEY from settings.
    If the default dev key is set, all requests are allowed (dev mode).

    Args:
        credentials: The Bearer token from the Authorization header.

    Returns:
        str: The validated API key.

    Raises:
        HTTPException: 401 if no token provided, 403 if token is invalid.
    """
    # Dev mode: if using the default key, skip auth
    if settings.api_key == "td_dev_key":
        return "dev"

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Use 'Authorization: Bearer td_xxx'."
        )

    if credentials.credentials != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key."
        )

    return credentials.credentials
