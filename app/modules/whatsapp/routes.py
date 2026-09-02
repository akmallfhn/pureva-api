from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import session_scope
from app.modules.whatsapp.schema import WAWebhookBody
from app.modules.whatsapp.service import WhatsAppWebhookService
from app.shared.security import verify_meta_signature

ServiceFactory = Callable[[AsyncSession], WhatsAppWebhookService]


def register_whatsapp_routes(rg: APIRouter, build_service: ServiceFactory) -> None:
    router = APIRouter(prefix="/webhook/whatsapp", tags=["webhook:whatsapp-meta"])

    async def process(payload: WAWebhookBody) -> None:
        # Session sendiri: session milik request sudah ditutup waktu background task jalan.
        async with session_scope() as session:
            await build_service(session).process(payload)

    @router.get("/callback")
    async def verify_webhook(
        hub_mode: str = Query(default="", alias="hub.mode"),
        hub_verify_token: str = Query(default="", alias="hub.verify_token"),
        hub_challenge: str = Query(default="", alias="hub.challenge"),
    ) -> PlainTextResponse:
        if (
            hub_mode == "subscribe"
            and settings.meta_webhook_verify_token
            and hub_verify_token == settings.meta_webhook_verify_token
        ):
            return PlainTextResponse(hub_challenge)
        raise HTTPException(status_code=403, detail="Forbidden")

    @router.post("/callback")
    async def receive_callback(request: Request, background_tasks: BackgroundTasks) -> Response:
        raw_body = await request.body()

        if settings.meta_app_secret:
            signature = request.headers.get("x-hub-signature-256", "")
            if not verify_meta_signature(raw_body, signature, settings.meta_app_secret):
                raise HTTPException(status_code=401, detail="Invalid signature")

        payload = WAWebhookBody.model_validate_json(raw_body)
        if payload.object != "whatsapp_business_account":
            return Response(status_code=200)

        # Meta mengulang kirim kalau tidak dibalas cepat, jadi persist-nya di background.
        background_tasks.add_task(process, payload)
        return Response(status_code=200)

    rg.include_router(router)
