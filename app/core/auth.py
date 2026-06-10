from fastapi import Header, HTTPException

from app.core.config import settings


async def verify_token(authorization: str = Header(...)) -> None:
    if not settings.agent_secret_key:
        raise HTTPException(status_code=500, detail="Server misconfigured: missing secret key")
    if authorization != f"Bearer {settings.agent_secret_key}":
        raise HTTPException(status_code=401, detail="Unauthorized")
