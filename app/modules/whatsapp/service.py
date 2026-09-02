"""Event webhook Meta -> Postgres; commit per pesan biar satu gagal tak menjatuhkan sisanya."""

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tenant.entity import Tenant
from app.modules.tenant.repository import TenantRepository
from app.modules.whatsapp.entity import (
    CHAT_STATUS_DELIVERED,
    CHAT_STATUS_FAILED,
    CHAT_STATUS_READ,
    CHAT_STATUS_SENT,
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    SENDER_TYPE_ADMIN,
    SENDER_TYPE_USER,
)
from app.modules.whatsapp.meta_client import MetaMediaClient
from app.modules.whatsapp.repository import WaChatRepository, WaConversationRepository
from app.modules.whatsapp.schema import WAWebhookBody
from app.shared.storage import SupabaseStorage

logger = logging.getLogger(__name__)

SUPPORTED_MESSAGE_TYPES = {"audio", "contacts", "document", "image", "sticker", "text", "video"}
# Tipe pesan yang attachment-nya beneran file (didownload dari Meta & diupload ke Storage).
MEDIA_MESSAGE_TYPES = {"audio", "document", "image", "sticker", "video"}

HANDLED_FIELDS = ("messages", "smb_message_echoes")

_STATUS_MAP = {
    "sent": CHAT_STATUS_SENT,
    "delivered": CHAT_STATUS_DELIVERED,
    "read": CHAT_STATUS_READ,
    "played": CHAT_STATUS_READ,
    "failed": CHAT_STATUS_FAILED,
}
_STATUS_TIMESTAMP_FIELD = {
    CHAT_STATUS_SENT: "sent_at",
    CHAT_STATUS_DELIVERED: "delivered_at",
    CHAT_STATUS_READ: "read_at",
    CHAT_STATUS_FAILED: "failed_at",
}


def unix_to_datetime(value: Any) -> datetime:
    try:
        ts = int(value)
    except (TypeError, ValueError):
        ts = 0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class WhatsAppWebhookService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        tenants: TenantRepository,
        conversations: WaConversationRepository,
        chats: WaChatRepository,
        media: MetaMediaClient,
        storage: SupabaseStorage,
    ) -> None:
        self._session = session
        self._tenants = tenants
        self._conversations = conversations
        self._chats = chats
        self._media = media
        self._storage = storage

    async def process(self, payload: WAWebhookBody) -> None:
        for entry in payload.entry:
            for change in entry.changes:
                if change.field not in HANDLED_FIELDS:
                    continue

                value = change.value
                phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
                if not phone_number_id:
                    continue

                try:
                    tenant = await self._tenants.find_by_wa_phone_number_id(phone_number_id)
                except Exception:
                    await self._session.rollback()
                    logger.exception(f"wa-meta webhook: tenant lookup failed for {phone_number_id}")
                    continue

                if tenant is None:
                    logger.info(f"wa-meta webhook: no tenant for phone_number_id={phone_number_id}")
                    continue

                if change.field == "smb_message_echoes":
                    await self._handle_echoes(tenant, value)
                else:
                    await self._handle_messages(tenant, phone_number_id, value)

                await self._handle_statuses(tenant, value)

    # --- Pesan masuk dari pelanggan ----------------------------------------------
    async def _handle_messages(
        self, tenant: Tenant, phone_number_id: str, value: dict[str, Any]
    ) -> None:
        messages = value.get("messages") or []
        if not messages:
            return

        contacts = value.get("contacts") or []
        full_name = contacts[0].get("profile", {}).get("name", "") if contacts else ""

        for msg in messages:
            msg_type = msg.get("type")
            if msg_type not in SUPPORTED_MESSAGE_TYPES:
                continue

            is_text = msg_type == "text"
            attachment = msg.get(msg_type) if not is_text else None
            if attachment is not None and msg_type in MEDIA_MESSAGE_TYPES:
                attachment = await self._save_media_attachment(
                    tenant=tenant,
                    phone_number_id=phone_number_id,
                    media_type=msg_type,
                    attachment=attachment,
                )

            wam_id = msg.get("id", "")
            try:
                await self._append_inbound_chat(
                    tenant_id=tenant.id,
                    full_name=full_name,
                    phone_number=msg.get("from", ""),
                    wam_id=wam_id,
                    msg_type=msg_type,
                    message=msg.get("text", {}).get("body", "") if is_text else "",
                    attachment=attachment,
                    sent_at_unix=msg.get("timestamp"),
                    context_wam_id=(msg.get("context") or {}).get("id"),
                )
                await self._session.commit()
            except Exception:
                await self._session.rollback()
                logger.exception(f"wa-meta webhook: failed to store wam_id={wam_id}")

    async def _append_inbound_chat(
        self,
        *,
        tenant_id: str,
        full_name: str,
        phone_number: str,
        wam_id: str,
        msg_type: str,
        message: str,
        attachment: Any | None,
        sent_at_unix: Any,
        context_wam_id: str | None,
    ) -> None:
        conv = await self._conversations.find_or_create(
            tenant_id=tenant_id, full_name=full_name, phone_number=phone_number
        )

        reply_to_id = None
        if context_wam_id:
            replied_to = await self._chats.find_by_wam_id(context_wam_id, conv_id=conv.id)
            reply_to_id = replied_to.id if replied_to else None

        await self._chats.create(
            conv_id=conv.id,
            wam_id=wam_id,
            direction=DIRECTION_INBOUND,
            sender_type=SENDER_TYPE_USER,
            msg_type=msg_type,
            message=message,
            attachment=attachment,
            reply_to_id=reply_to_id,
            created_at=unix_to_datetime(sent_at_unix),
        )

    # --- Echo pesan yang dikirim staff dari WhatsApp Business App (coexistence) ---
    async def _handle_echoes(self, tenant: Tenant, value: dict[str, Any]) -> None:
        for echo in value.get("message_echoes") or []:
            msg_type = echo.get("type")
            if msg_type not in SUPPORTED_MESSAGE_TYPES:
                continue

            is_text = msg_type == "text"
            wam_id = echo.get("id", "")
            try:
                conv = await self._conversations.find_or_create(
                    tenant_id=tenant.id, full_name="", phone_number=echo.get("to", "")
                )
                await self._chats.create(
                    conv_id=conv.id,
                    wam_id=wam_id,
                    direction=DIRECTION_OUTBOUND,
                    sender_type=SENDER_TYPE_ADMIN,
                    msg_type=msg_type,
                    message=echo.get("text", {}).get("body", "") if is_text else "",
                    attachment=echo.get(msg_type) if not is_text else None,
                    created_at=unix_to_datetime(echo.get("timestamp")),
                )
                await self._session.commit()
            except Exception:
                await self._session.rollback()
                logger.exception(f"wa-meta webhook: failed to store echo {wam_id}")

    # --- Status delivery pesan keluar ---------------------------------------------
    async def _handle_statuses(self, tenant: Tenant, value: dict[str, Any]) -> None:
        for status in value.get("statuses") or []:
            wam_id = status.get("id", "")
            try:
                await self._update_status(
                    tenant_id=tenant.id,
                    phone_number=status.get("recipient_id", ""),
                    wam_id=wam_id,
                    status=status.get("status", ""),
                    updated_at_unix=status.get("timestamp"),
                )
                await self._session.commit()
            except Exception:
                await self._session.rollback()
                logger.exception(f"wa-meta webhook: failed to update status {wam_id}")

    async def _update_status(
        self,
        *,
        tenant_id: str,
        phone_number: str,
        wam_id: str,
        status: str,
        updated_at_unix: Any,
    ) -> None:
        mapped = _STATUS_MAP.get(status)
        if not mapped:
            return

        updated_at = unix_to_datetime(updated_at_unix)
        timestamp_field = _STATUS_TIMESTAMP_FIELD[mapped]

        chat = await self._chats.find_by_wam_id(wam_id)
        if chat is not None:
            chat.status = mapped
            setattr(chat, timestamp_field, updated_at)
            return

        # Pesan keluar lewat WhatsApp Business App (coexistence) yang belum pernah tercatat.
        conv = await self._conversations.find_or_create(
            tenant_id=tenant_id, full_name="", phone_number=phone_number
        )
        await self._chats.create(
            conv_id=conv.id,
            wam_id=wam_id,
            direction=DIRECTION_OUTBOUND,
            sender_type=SENDER_TYPE_ADMIN,
            msg_type="text",
            message="",
            status=mapped,
            created_at=updated_at,
            **{timestamp_field: updated_at},
        )

    # --- Attachment: download dari Meta, simpan ke Supabase Storage ----------------
    async def _save_media_attachment(
        self,
        *,
        tenant: Tenant,
        phone_number_id: str,
        media_type: str,
        attachment: dict[str, Any],
    ) -> dict[str, Any]:
        """Sisipkan `storage_url` ke attachment. Media gagal disimpan tidak membatalkan pesannya."""
        media_id = attachment.get("id")
        access_token = tenant.wa_access_token or ""
        if not access_token or not media_id or not self._storage.enabled:
            return attachment

        try:
            content, mime_type = await self._media.fetch(
                access_token=access_token, phone_number_id=phone_number_id, media_id=media_id
            )
            object_path = self._storage.object_path(
                tenant.slug or tenant.id, media_type, media_id, mime_type
            )
            storage_url = await self._storage.upload(object_path, content, mime_type)
            return {**attachment, "storage_url": storage_url}
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            logger.exception(f"wa-meta webhook: failed to save media {media_id} ({body})")
            return attachment
        except Exception:
            logger.exception(f"wa-meta webhook: failed to save media {media_id}")
            return attachment
