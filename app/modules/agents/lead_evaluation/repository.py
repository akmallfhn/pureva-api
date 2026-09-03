"""Baca konteks percakapan untuk dinilai, lalu tulis balik empat kolom hasil penilaian."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.agents.lead_evaluation.schema import ConversationContext

# Pesan panjang dipotong supaya satu percakapan ramai tidak menghabiskan context window.
MAX_MESSAGE_CHARS = 500

# Jumlah pesan terakhir yang dikirim ke LLM; percakapan lebih panjang dipotong di ujung lama.
MAX_CHATS = 200

WRITABLE_COLUMNS = ("brand_name", "project_value", "lead_status", "note")

_SPEAKER = {"inbound": "Pelanggan", "outbound": "Kami"}


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.stat_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _line(row: dict[str, Any], tz: ZoneInfo) -> str:
    at: datetime = row["created_at"]
    speaker = _SPEAKER.get(row["direction"], row["direction"])
    body = (row["message"] or "").strip()
    if not body:
        # Sticker/gambar/dokumen tidak punya teks; tipe pesannya saja sudah jadi konteks.
        body = f"[kiriman {row['type']}]"
    elif len(body) > MAX_MESSAGE_CHARS:
        body = body[:MAX_MESSAGE_CHARS] + "..."
    return f"[{at.astimezone(tz):%Y-%m-%d %H:%M}] {speaker}: {body}"


class LeadEvalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_context(self, conv_id: str) -> ConversationContext | None:
        conv = (
            (
                await self._session.execute(
                    text("""
                    SELECT id, full_name, phone_number, brand_name, project_value,
                           lead_status::text AS lead_status, note
                    FROM wa_conversations
                    WHERE id = :conv_id
                """),
                    {"conv_id": conv_id},
                )
            )
            .mappings()
            .first()
        )
        if conv is None:
            return None

        # Ambil dari yang terbaru supaya percakapan panjang terpotong di ujung lama, bukan baru.
        rows = (
            (
                await self._session.execute(
                    text("""
                    SELECT direction::text AS direction, type::text AS type, message, created_at
                    FROM wa_chats
                    WHERE conv_id = :conv_id
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                """),
                    {"conv_id": conv_id, "limit": MAX_CHATS},
                )
            )
            .mappings()
            .all()
        )

        tz = _tz()
        chats = list(reversed([dict(r) for r in rows]))
        return ConversationContext(
            conv_id=conv["id"].strip(),
            full_name=conv["full_name"],
            phone_number=conv["phone_number"],
            brand_name=conv["brand_name"],
            project_value=conv["project_value"],
            lead_status=conv["lead_status"],
            note=conv["note"],
            transcript="\n".join(_line(c, tz) for c in chats),
            chat_count=len(chats),
            text_count=sum(1 for c in chats if (c["message"] or "").strip()),
        )

    async def apply(self, conv_id: str, changes: dict[str, Any]) -> None:
        if not changes:
            return

        unknown = set(changes) - set(WRITABLE_COLUMNS)
        if unknown:
            raise ValueError(f"kolom di luar cakupan evaluator: {sorted(unknown)}")

        # CAST, bukan sintaks "::" — SQLAlchemy membaca ":lead_status::wa_..." sebagai bindparam.
        assignments = ", ".join(
            f"{c} = CAST(:{c} AS wa_lead_status_enum)" if c == "lead_status" else f"{c} = :{c}"
            for c in changes
        )

        await self._session.execute(
            text(f"UPDATE wa_conversations SET {assignments} WHERE id = :conv_id"),
            {**changes, "conv_id": conv_id},
        )
        await self._session.commit()
