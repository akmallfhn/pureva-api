from datetime import datetime

from sqlalchemy import CHAR, String, Text, text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"

# create_type=False: enum-nya sudah ada di Postgres, dimiliki schema Prisma pureva-ai.
STATUS_ENUM = ENUM(STATUS_ACTIVE, STATUS_INACTIVE, name="status_enum", create_type=False)


class Tenant(Base):
    """Satu klinik/brand dengan nomor WhatsApp Business-nya sendiri."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(CHAR(21), primary_key=True, server_default=text("nanoid()"))
    name: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(STATUS_ENUM, server_default=text("'active'"))
    wa_phone_number_id: Mapped[str | None] = mapped_column(String)
    wa_business_id: Mapped[str | None] = mapped_column(String)
    wa_access_token: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )
