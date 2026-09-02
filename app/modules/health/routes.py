from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session


def register_health_routes(rg: APIRouter) -> None:
    router = APIRouter(tags=["health"])

    @router.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @router.get("/health/db")
    async def health_db(session: AsyncSession = Depends(get_session)) -> dict:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "reachable"}

    rg.include_router(router)
