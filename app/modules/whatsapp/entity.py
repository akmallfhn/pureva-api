from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, BigInteger, ForeignKey, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import ENUM, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DIRECTION_INBOUND = "inbound"
DIRECTION_OUTBOUND = "outbound"

SENDER_TYPE_USER = "user"
SENDER_TYPE_ADMIN = "admin"

MODE_AI = "ai"
MODE_HUMAN = "human"

CHAT_TYPES = (
    "audio",
    "button",
    "contacts",
    "document",
    "edit",
    "image",
    "interactive",
    "location",
    "order",
    "reaction",
    "revoke",
    "sticker",
    "system",
    "text",
    "unsupported",
    "video",
    "template",
)

CHAT_STATUS_SENT = "sent"
CHAT_STATUS_DELIVERED = "delivered"
CHAT_STATUS_READ = "read"
CHAT_STATUS_FAILED = "failed"

# create_type=False: enum-nya sudah ada di Postgres, dimiliki schema Prisma pureva-ai.
LEAD_STATUS_ENUM = ENUM(
    "cold",
    "qualified",
    "rate_card_sent",
    "negotiation",
    "closed",
    name="wa_lead_status_enum",
    create_type=False,
)
MODE_ENUM = ENUM(MODE_AI, MODE_HUMAN, name="wa_mode_enum", create_type=False)
DIRECTION_ENUM = ENUM(
    DIRECTION_INBOUND, DIRECTION_OUTBOUND, name="wac_direction_enum", create_type=False
)
SENDER_TYPE_ENUM = ENUM(
    SENDER_TYPE_USER, SENDER_TYPE_ADMIN, name="wac_sender_type_enum", create_type=False
)
CHAT_TYPE_ENUM = ENUM(*CHAT_TYPES, name="wac_type_enum", create_type=False)
CHAT_STATUS_ENUM = ENUM(
    CHAT_STATUS_SENT,
    CHAT_STATUS_DELIVERED,
    CHAT_STATUS_READ,
    CHAT_STATUS_FAILED,
    name="wac_status_enum",
    create_type=False,
)


class WaConversation(Base):
    """Satu thread WhatsApp antara tenant dan satu nomor pelanggan."""

    __tablename__ = "wa_conversations"

    id: Mapped[str] = mapped_column(CHAR(21), primary_key=True, server_default=text("nanoid()"))
    tenant_id: Mapped[str] = mapped_column(CHAR(21), ForeignKey("tenants.id"))
    full_name: Mapped[str] = mapped_column(String)
    phone_number: Mapped[str] = mapped_column(String)
    brand_name: Mapped[str | None] = mapped_column(String)
    handler_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    lead_status: Mapped[str] = mapped_column(LEAD_STATUS_ENUM, server_default=text("'cold'"))
    project_value: Mapped[int | None] = mapped_column(BigInteger)
    winning_rate: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    mode: Mapped[str] = mapped_column(MODE_ENUM, server_default=text("'human'"))
    note: Mapped[str | None] = mapped_column(String)
    last_read_id: Mapped[str | None] = mapped_column(CHAR(21))
    created_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )


class WaChat(Base):
    """Satu pesan WhatsApp, arah masuk maupun keluar."""

    __tablename__ = "wa_chats"

    id: Mapped[str] = mapped_column(CHAR(21), primary_key=True, server_default=text("nanoid()"))
    conv_id: Mapped[str] = mapped_column(CHAR(21), ForeignKey("wa_conversations.id"))
    wam_id: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(DIRECTION_ENUM)
    sender_type: Mapped[str] = mapped_column(SENDER_TYPE_ENUM)
    reply_to_id: Mapped[str | None] = mapped_column(CHAR(21), ForeignKey("wa_chats.id"))
    type: Mapped[str] = mapped_column(CHAT_TYPE_ENUM)
    message: Mapped[str] = mapped_column(String)
    attachment: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))
    status: Mapped[str | None] = mapped_column(CHAT_STATUS_ENUM)
    sent_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    read_at: Mapped[datetime | None]
    failed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )
