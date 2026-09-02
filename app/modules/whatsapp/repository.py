from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.whatsapp.entity import WaChat, WaConversation


class WaConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_or_create(
        self, *, tenant_id: str, full_name: str, phone_number: str
    ) -> WaConversation:
        """Upsert; sandarkan ke unique (tenant_id, phone_number) karena event Meta bisa balapan."""
        stmt = (
            pg_insert(WaConversation)
            .values(tenant_id=tenant_id, full_name=full_name, phone_number=phone_number)
            .on_conflict_do_update(
                index_elements=[WaConversation.tenant_id, WaConversation.phone_number],
                set_={
                    "full_name": func.coalesce(
                        func.nullif(pg_insert(WaConversation).excluded.full_name, ""),
                        WaConversation.full_name,
                    )
                },
            )
            .returning(WaConversation)
        )
        result = await self._session.execute(stmt, execution_options={"populate_existing": True})
        return result.scalars().one()


class WaChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_wam_id(self, wam_id: str, conv_id: str | None = None) -> WaChat | None:
        stmt = select(WaChat).where(WaChat.wam_id == wam_id)
        if conv_id is not None:
            stmt = stmt.where(WaChat.conv_id == conv_id)
        return (await self._session.execute(stmt.limit(1))).scalars().first()

    async def create(
        self,
        *,
        conv_id: str,
        wam_id: str,
        direction: str,
        sender_type: str,
        msg_type: str,
        message: str,
        attachment: Any | None = None,
        reply_to_id: str | None = None,
        status: str | None = None,
        created_at: datetime | None = None,
        **timestamps: datetime,
    ) -> WaChat:
        chat = WaChat(
            conv_id=conv_id,
            wam_id=wam_id,
            direction=direction,
            sender_type=sender_type,
            type=msg_type,
            message=message,
            attachment=attachment,
            reply_to_id=reply_to_id,
            status=status,
            **timestamps,
        )
        if created_at is not None:
            chat.created_at = created_at
        self._session.add(chat)
        await self._session.flush()
        return chat
