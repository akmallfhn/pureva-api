"""Satu-satunya tempat wiring: semua repository/service/routes dirakit di sini."""

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import dispose_engine, init_engine
from app.modules.agents.lead_evaluation.llm import evaluate_with_llm
from app.modules.agents.lead_evaluation.repository import LeadEvalRepository
from app.modules.agents.lead_evaluation.service import LeadEvaluationService
from app.modules.agents.llm import is_configured
from app.modules.health.routes import register_health_routes
from app.modules.stat.repository import StatRepository
from app.modules.stat.routes import register_stat_routes
from app.modules.stat.service import StatService
from app.modules.tenant.repository import TenantRepository
from app.modules.whatsapp.meta_client import MetaMediaClient
from app.modules.whatsapp.repository import WaChatRepository, WaConversationRepository
from app.modules.whatsapp.routes import register_whatsapp_routes
from app.modules.whatsapp.service import WhatsAppWebhookService
from app.shared.http import close_http_client, http_client
from app.shared.response import ApiError, api_error_handler, error
from app.shared.storage import SupabaseStorage

logger = logging.getLogger(__name__)


def build_whatsapp_service(session: AsyncSession) -> WhatsAppWebhookService:
    client = http_client()
    return WhatsAppWebhookService(
        session,
        tenants=TenantRepository(session),
        conversations=WaConversationRepository(session),
        chats=WaChatRepository(session),
        media=MetaMediaClient(client, settings.graph_api_version),
        storage=SupabaseStorage(
            client,
            base_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_bucket,
        ),
    )


def build_lead_evaluation_service(session: AsyncSession) -> LeadEvaluationService:
    return LeadEvaluationService(
        repo=LeadEvalRepository(session),
        evaluate=evaluate_with_llm,
        enabled=is_configured(),
    )


def build_stat_service(session: AsyncSession) -> StatService:
    return StatService(stats=StatRepository(session), tenants=TenantRepository(session))


async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Samakan bentuk error validasi dengan envelope, bukan 422 bawaan FastAPI."""
    field = ".".join(str(p) for p in exc.errors()[0]["loc"][1:]) or "request body"
    return error(400, f"invalid request: {field}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Gagal cepat di startup daripada baru ketahuan waktu event pertama dari Meta masuk.
    init_engine()

    yield

    await close_http_client()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    register_health_routes(app.router)

    api = APIRouter(prefix="/api/v1")
    register_whatsapp_routes(api, build_whatsapp_service, build_lead_evaluation_service)
    register_stat_routes(api, build_stat_service)
    app.include_router(api)

    return app
