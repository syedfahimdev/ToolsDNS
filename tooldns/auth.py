"""
auth.py — API key authentication for ToolDNS.

Supports two key types:
  1. Admin key (TOOLDNS_API_KEY env var) — unlimited, full access
  2. Named sub-keys (stored in api_keys table) — per-key usage tracking + limits

Usage:
    from tooldns.auth import require_api_key, init_auth
    init_auth(database)  # call at startup

    @app.get("/v1/tools", dependencies=[Depends(require_api_key)])
    def list_tools(): ...
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from tooldns.config import settings

security = HTTPBearer(auto_error=False)

_database = None


def init_auth(database) -> None:
    """Inject the database dependency (called at startup)."""
    global _database
    _database = database


async def require_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    FastAPI dependency that validates the Bearer token.

    Accepts:
      - The admin key (TOOLDNS_API_KEY) — unlimited access
      - Any active sub-key from the api_keys table — limited by monthly_limit

    Returns:
        dict: Key info including name, plan, is_admin.

    Raises:
        HTTPException 401: No token provided.
        HTTPException 403: Invalid or revoked key.
        HTTPException 429: Monthly limit exceeded.
    """
    # Dev mode — skip auth with default key
    if settings.api_key == "td_dev_key":
        return {"key": "dev", "name": "dev", "plan": "admin", "is_admin": True}

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Add 'Authorization: Bearer td_xxx' header."
        )

    token = credentials.credentials

    # Admin key — no limits, no DB lookup needed
    if token == settings.api_key:
        return {"key": token, "name": "admin", "plan": "admin", "is_admin": True}

    # Sub-key — look up in database
    if _database is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Auth service not ready.")

    key_info = _database.get_api_key(token)
    if not key_info:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Invalid API key.")
    if not key_info["is_active"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="API key has been revoked.")

    # Enforce monthly limit (0 = unlimited)
    if key_info["monthly_limit"] > 0 and key_info["search_count"] >= key_info["monthly_limit"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Monthly search limit ({key_info['monthly_limit']}) exceeded. Upgrade your plan."
        )

    # Track usage asynchronously (don't block response)
    import threading
    threading.Thread(target=_database.increment_key_usage, args=(token,), daemon=True).start()

    return {**key_info, "is_admin": False}
