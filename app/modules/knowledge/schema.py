"""Bentuk request/response modul Knowledge dan state graph agent-nya."""

from typing import Any, TypedDict

from pydantic import BaseModel, Field

MAX_MESSAGE_CHARS = 4_000
MAX_TITLE_CHARS = 120


class TenantRequest(BaseModel):
    tenant_id: str


class ConversationListRequest(TenantRequest):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1)


class ConversationRequest(TenantRequest):
    conv_id: str


class ConversationCreateRequest(TenantRequest):
    title: str = Field(default="", max_length=MAX_TITLE_CHARS)


class ConversationRenameRequest(ConversationRequest):
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)


class ChatRequest(TenantRequest):
    """Prompt dari dashboard. conv_id kosong berarti thread baru dibuat lebih dulu."""

    conv_id: str | None = None
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class RetrievedSource(BaseModel):
    """Satu langkah retrieval yang benar-benar dijalankan, dikirim balik sebagai jejak."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class AgentState(TypedDict, total=False):
    """State graph: riwayat percakapan, pesan LLM, dan jejak retrieval yang terkumpul."""

    tenant_id: str
    question: str
    history: list[dict[str, str]]
    messages: list[Any]
    sources: list[RetrievedSource]
    rounds: int
    answer: str
    error: str | None
