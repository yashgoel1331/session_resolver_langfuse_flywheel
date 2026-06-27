import hmac

from fastapi import Header, HTTPException, status

from app.config import settings


async def verify_session_finder_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Protect internal routes with a simple shared API key."""
    expected = (settings.session_finder_api_key or "").strip()

    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SESSION_FINDER_API_KEY is not configured on the server.",
        )

    provided = (x_api_key or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header.",
        )
