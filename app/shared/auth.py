from fastapi import Header

from app.core.config import settings
from app.shared.response import ApiError

UNAUTHORIZED = "missing or invalid authorization header"


async def verify_client_secret(authorization: str = Header(default="")) -> None:
    """Bearer token statis untuk client dashboard, nilainya dari CLIENT_SECRET."""
    if not settings.client_secret:
        raise ApiError(500, "an unexpected error occurred")
    if authorization != f"Bearer {settings.client_secret}":
        raise ApiError(401, UNAUTHORIZED)
