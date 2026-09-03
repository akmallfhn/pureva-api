from collections.abc import Callable

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.stat.schema import (
    BrandListRequest,
    ListRequest,
    ResponseTimeRequest,
    StatRequest,
    SummaryRequest,
)
from app.modules.stat.service import StatService
from app.shared.auth import verify_client_secret
from app.shared.response import success

ServiceFactory = Callable[[AsyncSession], StatService]


def register_stat_routes(rg: APIRouter, build_service: ServiceFactory) -> None:
    router = APIRouter(
        prefix="/stats", tags=["stats"], dependencies=[Depends(verify_client_secret)]
    )

    def service(session: AsyncSession = Depends(get_session)) -> StatService:
        return build_service(session)

    @router.post("/summary")
    async def summary(req: SummaryRequest, svc: StatService = Depends(service)) -> Response:
        return success(200, "summary retrieved successfully", await svc.summary(req))

    @router.post("/chats-volume")
    async def chats_volume(req: StatRequest, svc: StatService = Depends(service)) -> Response:
        return success(200, "chats volume retrieved successfully", await svc.chats_volume(req))

    @router.post("/response-time")
    async def response_time(
        req: ResponseTimeRequest, svc: StatService = Depends(service)
    ) -> Response:
        return success(200, "response time retrieved successfully", await svc.response_time(req))

    @router.post("/inbound-heatmap")
    async def inbound_heatmap(req: StatRequest, svc: StatService = Depends(service)) -> Response:
        return success(
            200, "inbound heatmap retrieved successfully", await svc.inbound_heatmap(req)
        )

    @router.post("/lead-status")
    async def lead_status(req: StatRequest, svc: StatService = Depends(service)) -> Response:
        return success(200, "lead status retrieved successfully", await svc.lead_status(req))

    @router.post("/unanswered/list")
    async def unanswered(req: ListRequest, svc: StatService = Depends(service)) -> Response:
        return success(
            200, "unanswered conversations retrieved successfully", await svc.unanswered(req)
        )

    @router.post("/needs-action/list")
    async def needs_action(req: BrandListRequest, svc: StatService = Depends(service)) -> Response:
        return success(
            200, "needs action conversations retrieved successfully", await svc.needs_action(req)
        )

    rg.include_router(router)
