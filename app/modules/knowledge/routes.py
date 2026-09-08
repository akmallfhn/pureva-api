import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.knowledge.queue import JobChannel
from app.modules.knowledge.schema import (
    ChatRequest,
    ConversationCreateRequest,
    ConversationListRequest,
    ConversationRenameRequest,
    ConversationRequest,
)
from app.modules.knowledge.service import KnowledgeService
from app.shared.auth import verify_client_secret
from app.shared.response import success

logger = logging.getLogger(__name__)

# Proxy memutus koneksi yang diam; retrieval bisa hening belasan detik sebelum token pertama.
KEEPALIVE_SECONDS = 15.0

ServiceFactory = Callable[[AsyncSession], KnowledgeService]


def register_knowledge_routes(rg: APIRouter, build_service: ServiceFactory) -> None:
    router = APIRouter(
        prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(verify_client_secret)]
    )

    def service(session: AsyncSession = Depends(get_session)) -> KnowledgeService:
        return build_service(session)

    @router.post("/conversations")
    async def create_conversation(
        req: ConversationCreateRequest, svc: KnowledgeService = Depends(service)
    ) -> Response:
        return success(201, "conversation created successfully", await svc.create_conversation(req))

    @router.post("/conversations/list")
    async def list_conversations(
        req: ConversationListRequest, svc: KnowledgeService = Depends(service)
    ) -> Response:
        return success(
            200, "conversations retrieved successfully", await svc.list_conversations(req)
        )

    @router.post("/conversations/detail")
    async def get_conversation(
        req: ConversationRequest, svc: KnowledgeService = Depends(service)
    ) -> Response:
        return success(200, "conversation retrieved successfully", await svc.get_conversation(req))

    @router.post("/conversations/update")
    async def rename_conversation(
        req: ConversationRenameRequest, svc: KnowledgeService = Depends(service)
    ) -> Response:
        return success(200, "conversation updated successfully", await svc.rename_conversation(req))

    @router.post("/conversations/delete")
    async def delete_conversation(
        req: ConversationRequest, svc: KnowledgeService = Depends(service)
    ) -> Response:
        return success(200, "conversation deleted successfully", await svc.delete_conversation(req))

    @router.post("/chat/stream")
    async def chat_stream(
        req: ChatRequest, svc: KnowledgeService = Depends(service)
    ) -> StreamingResponse:
        # Job dititipkan sebelum stream dibuka, supaya penolakan masih dapat envelope biasa.
        job, channel = await svc.submit(req)

        return StreamingResponse(
            _events(job.conv_id, channel),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                # Nginx di depan Railway mem-buffer SSE tanpa header ini.
                "X-Accel-Buffering": "no",
            },
        )

    rg.include_router(router)


async def _events(conv_id: str, channel: JobChannel) -> AsyncIterator[str]:
    """Terjemahkan event job jadi SSE. Payload tiap event selalu satu string JSON."""
    yield _frame("conversation", conv_id)

    keepalive = asyncio.create_task(_keepalive(channel))
    try:
        async for event, data in channel:
            # Komentar SSE menahan proxy memutus koneksi tanpa terbaca sebagai event.
            yield ": keepalive\n\n" if event == "ping" else _frame(event, data)
    finally:
        keepalive.cancel()


async def _keepalive(channel: JobChannel) -> None:
    while True:
        await asyncio.sleep(KEEPALIVE_SECONDS)
        await channel.publish("ping", "")


def _frame(event: str, data: str) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
