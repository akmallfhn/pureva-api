"""Entry point modul Knowledge: CRUD thread, penitipan job ke antrean, dan eksekusi job."""

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.modules.knowledge.entity import (
    ROLE_ASSISTANT,
    ROLE_USER,
    STATUS_QUEUED,
    STATUS_STREAMING,
    KbChat,
)
from app.modules.knowledge.graph import build_knowledge_graph
from app.modules.knowledge.llm import generate_title
from app.modules.knowledge.prompts import SYSTEM_PROMPT
from app.modules.knowledge.queue import ChatJob, ChatQueue, JobChannel, QueueFullError
from app.modules.knowledge.repository import KnowledgeRepository, RetrievalRepository
from app.modules.knowledge.schema import (
    AgentState,
    ChatRequest,
    ConversationCreateRequest,
    ConversationListRequest,
    ConversationRenameRequest,
    ConversationRequest,
)
from app.modules.knowledge.tools import ToolBox
from app.modules.stat.service import StatService
from app.modules.tenant.repository import TenantRepository
from app.shared import pagination
from app.shared.response import ApiError

logger = logging.getLogger(__name__)

# Giliran sebelumnya yang ikut jadi konteks; cukup untuk pertanyaan lanjutan tanpa membengkak.
HISTORY_TURNS = 10

# Judul sementara sebelum LLM memberi judul asli, supaya sidebar tidak pernah kosong.
FALLBACK_TITLE_CHARS = 60

_WEEKDAY = (
    "Senin",
    "Selasa",
    "Rabu",
    "Kamis",
    "Jumat",
    "Sabtu",
    "Minggu",
)


class KnowledgeService:
    """Dipakai route: semuanya jalan di session milik request."""

    def __init__(
        self,
        *,
        repo: KnowledgeRepository,
        tenants: TenantRepository,
        queue: ChatQueue,
        enabled: bool,
    ) -> None:
        self._repo = repo
        self._tenants = tenants
        self._queue = queue
        self._enabled = enabled

    async def _tenant(self, tenant_id: str) -> str:
        if not tenant_id.strip():
            raise ApiError(400, "tenant_id is required")
        if await self._tenants.find_by_id(tenant_id) is None:
            raise ApiError(404, "tenant not found")
        return tenant_id

    async def _owned(self, tenant_id: str, conv_id: str) -> str:
        await self._tenant(tenant_id)
        conv = await self._repo.find_conversation(tenant_id=tenant_id, conv_id=conv_id)
        if conv is None:
            raise ApiError(404, "conversation not found")
        return conv.id

    async def create_conversation(self, req: ConversationCreateRequest) -> dict[str, Any]:
        await self._tenant(req.tenant_id)
        conv = await self._repo.create_conversation(
            tenant_id=req.tenant_id, title=req.title.strip()
        )
        return _conversation(conv.id, conv.title, conv.created_at, conv.updated_at)

    async def list_conversations(self, req: ConversationListRequest) -> dict[str, Any]:
        await self._tenant(req.tenant_id)
        page, page_size = pagination.normalize(req.page, req.page_size)

        total = await self._repo.count_conversations(tenant_id=req.tenant_id)
        rows = await self._repo.list_conversations(
            tenant_id=req.tenant_id,
            limit=page_size,
            skip=pagination.offset(page, page_size),
        )
        return {
            "list": [_isoformat_times(row) for row in rows],
            "metapaging": pagination.meta(total, page, page_size),
        }

    async def get_conversation(self, req: ConversationRequest) -> dict[str, Any]:
        conv_id = await self._owned(req.tenant_id, req.conv_id)
        conv = await self._repo.find_conversation(tenant_id=req.tenant_id, conv_id=conv_id)
        assert conv is not None

        chats = await self._repo.list_chats(conv_id=conv_id)
        return {
            **_conversation(conv.id, conv.title, conv.created_at, conv.updated_at),
            "list": [_chat(c) for c in chats],
        }

    async def rename_conversation(self, req: ConversationRenameRequest) -> dict[str, Any]:
        conv_id = await self._owned(req.tenant_id, req.conv_id)
        await self._repo.rename_conversation(conv_id=conv_id, title=req.title.strip())
        return {"conv_id": conv_id, "title": req.title.strip()}

    async def delete_conversation(self, req: ConversationRequest) -> dict[str, Any]:
        conv_id = await self._owned(req.tenant_id, req.conv_id)
        await self._repo.delete_conversation(conv_id=conv_id)
        return {"conv_id": conv_id}

    async def submit(self, req: ChatRequest) -> tuple[ChatJob, JobChannel]:
        """Simpan pertanyaan, titipkan jawabannya ke antrean, kembalikan salurannya."""
        if not self._enabled:
            raise ApiError(503, "knowledge chat is not configured: OPENAI_API_KEY is missing")

        await self._tenant(req.tenant_id)
        question = req.message.strip()
        if not question:
            raise ApiError(400, "message is required")

        is_new = not req.conv_id
        if is_new:
            conv = await self._repo.create_conversation(
                tenant_id=req.tenant_id, title=_fallback_title(question)
            )
            conv_id = conv.id
        else:
            conv_id = await self._owned(req.tenant_id, req.conv_id or "")

        asked = await self._repo.create_chat(conv_id=conv_id, role=ROLE_USER, message=question)
        answer = await self._repo.create_chat(
            conv_id=conv_id, role=ROLE_ASSISTANT, status=STATUS_QUEUED
        )

        job = ChatJob(
            tenant_id=req.tenant_id,
            conv_id=conv_id,
            question_id=asked.id,
            chat_id=answer.id,
            question=question,
            is_new_conversation=is_new,
        )
        try:
            channel = self._queue.submit(job)
        except QueueFullError:
            await self._repo.fail_chat(chat_id=answer.id, error="queue is full")
            raise ApiError(429, "too many questions in flight, try again shortly") from None

        return job, channel


class ChatRunner:
    """Dipakai worker antrean: satu job, satu session database sendiri."""

    def __init__(
        self,
        *,
        repo: KnowledgeRepository,
        retrieval: RetrievalRepository,
        stats: StatService,
    ) -> None:
        self._repo = repo
        self._retrieval = retrieval
        self._stats = stats

    async def run(self, job: ChatJob, channel: JobChannel) -> None:
        try:
            await self._execute(job, channel)
        finally:
            await channel.close()

    async def _execute(self, job: ChatJob, channel: JobChannel) -> None:
        await self._repo.set_status(chat_id=job.chat_id, status=STATUS_STREAMING)

        # Batasnya baris pertanyaan ini: yang lebih tua jadi konteks, pertanyaannya tidak ikut.
        history = await self._repo.history(
            conv_id=job.conv_id, before_id=job.question_id, limit=HISTORY_TURNS * 2
        )

        toolbox = ToolBox(tenant_id=job.tenant_id, stats=self._stats, retrieval=self._retrieval)
        graph = build_knowledge_graph(
            toolbox=toolbox,
            system_prompt=await self._system_prompt(job.tenant_id),
            emit=channel.publish,
        )

        state: AgentState = {
            "tenant_id": job.tenant_id,
            "question": job.question,
            "history": history,
            "rounds": 0,
        }
        result = await graph.ainvoke(state)

        answer = (result.get("answer") or "").strip()
        sources = [s.model_dump() for s in result.get("sources") or []]
        error = result.get("error")

        if not answer:
            reason = error or "model tidak mengembalikan jawaban"
            await self._repo.fail_chat(chat_id=job.chat_id, error=reason)
            await channel.publish("error", "jawaban gagal disusun")
            logger.warning(f"knowledge: job {job.id} produced no answer ({reason})")
            return

        await self._repo.finish_chat(chat_id=job.chat_id, message=answer, sources=sources)
        await self._repo.touch_conversation(conv_id=job.conv_id)

        if job.is_new_conversation:
            await self._retitle(job, channel)

        await channel.publish("done", job.chat_id)

    async def _retitle(self, job: ChatJob, channel: JobChannel) -> None:
        """Judul dibuat sesudah jawaban selesai supaya tidak menunda token pertama."""
        try:
            title = await generate_title(job.question)
        except Exception:
            logger.exception(f"knowledge: title failed for {job.conv_id}")
            return

        if not title:
            return
        await self._repo.rename_conversation(conv_id=job.conv_id, title=title)
        await channel.publish("title", title)

    async def _system_prompt(self, tenant_id: str) -> str:
        tz_name = settings.stat_timezone
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            tz, tz_name = ZoneInfo("UTC"), "UTC"

        now = datetime.now(tz)
        earliest, latest = await self._retrieval.activity_window(tenant_id=tenant_id)

        def stamp(at: datetime | None) -> str:
            return at.astimezone(tz).strftime("%Y-%m-%d %H:%M") if at else "belum ada"

        return SYSTEM_PROMPT.format(
            today=now.date().isoformat(),
            weekday=_WEEKDAY[now.weekday()],
            timezone=tz_name,
            earliest_activity=stamp(earliest),
            latest_activity=stamp(latest),
        )


def _fallback_title(question: str) -> str:
    single_line = " ".join(question.split())
    if len(single_line) <= FALLBACK_TITLE_CHARS:
        return single_line
    return single_line[:FALLBACK_TITLE_CHARS].rstrip() + "..."


def _conversation(
    conv_id: str, title: str, created_at: datetime, updated_at: datetime
) -> dict[str, Any]:
    return {
        "conv_id": conv_id,
        "title": title,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


def _chat(chat: KbChat) -> dict[str, Any]:
    return {
        "id": chat.id,
        "role": chat.role,
        "message": chat.message,
        "status": chat.status,
        "error": chat.error,
        "sources": chat.sources,
        "created_at": chat.created_at.isoformat(),
    }


def _isoformat_times(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}
