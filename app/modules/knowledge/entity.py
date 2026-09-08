from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import ENUM, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

STATUS_QUEUED = "queued"
STATUS_STREAMING = "streaming"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# create_type=False: enum-nya sudah ada di Postgres, dibuat lewat DDL di docs/db.
ROLE_ENUM = ENUM(ROLE_USER, ROLE_ASSISTANT, name="kbc_role_enum", create_type=False)
STATUS_ENUM = ENUM(
    STATUS_QUEUED,
    STATUS_STREAMING,
    STATUS_DONE,
    STATUS_FAILED,
    name="kbc_status_enum",
    create_type=False,
)


class KbConversation(Base):
    """Satu thread tanya-jawab internal tentang performa brand deal tenant."""

    __tablename__ = "kb_conversations"

    id: Mapped[str] = mapped_column(CHAR(21), primary_key=True, server_default=text("nanoid()"))
    tenant_id: Mapped[str] = mapped_column(CHAR(21), ForeignKey("tenants.id"))
    title: Mapped[str] = mapped_column(String, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )


class KbChat(Base):
    """Satu pesan dalam thread Knowledge; baris assistant ikut membawa status job-nya."""

    __tablename__ = "kb_chats"

    id: Mapped[str] = mapped_column(CHAR(21), primary_key=True, server_default=text("nanoid()"))
    conv_id: Mapped[str] = mapped_column(CHAR(21), ForeignKey("kb_conversations.id"))
    role: Mapped[str] = mapped_column(ROLE_ENUM)
    message: Mapped[str] = mapped_column(String, server_default=text("''"))
    # Hanya terisi pada baris assistant; baris user tidak pernah punya siklus hidup job.
    status: Mapped[str | None] = mapped_column(STATUS_ENUM)
    error: Mapped[str | None] = mapped_column(String)
    sources: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )
