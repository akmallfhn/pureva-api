"""CRUD thread Knowledge, plus retriever leksikal ke percakapan WhatsApp yang jadi korpusnya."""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.entity import (
    ROLE_ASSISTANT,
    STATUS_DONE,
    STATUS_FAILED,
    KbChat,
    KbConversation,
)

# Potongan pesan yang dikembalikan retriever; cukup untuk menilai konteks tanpa membanjiri prompt.
SNIPPET_CHARS = 240

# Batas transkrip satu percakapan supaya satu tool call tidak menghabiskan context window.
MAX_TRANSCRIPT_CHATS = 120
MAX_TRANSCRIPT_MESSAGE_CHARS = 400


class KnowledgeRepository:
    """Thread dan pesan chatbot Knowledge."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_conversation(self, *, tenant_id: str, title: str) -> KbConversation:
        conv = KbConversation(tenant_id=tenant_id, title=title)
        self._session.add(conv)
        await self._session.flush()
        await self._session.commit()
        return conv

    async def find_conversation(self, *, tenant_id: str, conv_id: str) -> KbConversation | None:
        stmt = (
            select(KbConversation)
            .where(KbConversation.id == conv_id, KbConversation.tenant_id == tenant_id)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def count_conversations(self, *, tenant_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(KbConversation)
            .where(KbConversation.tenant_id == tenant_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_conversations(
        self, *, tenant_id: str, limit: int, skip: int
    ) -> list[dict[str, Any]]:
        """Daftar thread + pratinjau pesan terakhir, supaya sidebar tidak perlu query kedua."""
        stmt = text("""
            SELECT
                v.id,
                v.title,
                v.created_at,
                v.updated_at,
                (SELECT COUNT(*) FROM kb_chats c WHERE c.conv_id = v.id) AS chat_count,
                (SELECT LEFT(c.message, 120) FROM kb_chats c
                 WHERE c.conv_id = v.id AND c.message <> ''
                 ORDER BY c.created_at DESC, c.id DESC LIMIT 1) AS last_message_preview,
                (SELECT c.created_at FROM kb_chats c
                 WHERE c.conv_id = v.id
                 ORDER BY c.created_at DESC, c.id DESC LIMIT 1) AS last_message_at
            FROM kb_conversations v
            WHERE v.tenant_id = :tenant_id
            ORDER BY v.updated_at DESC, v.id DESC
            LIMIT :limit OFFSET :skip
        """)
        result = await self._session.execute(
            stmt, {"tenant_id": tenant_id, "limit": limit, "skip": skip}
        )
        return [dict(row) for row in result.mappings().all()]

    async def rename_conversation(self, *, conv_id: str, title: str) -> None:
        await self._session.execute(
            update(KbConversation)
            .where(KbConversation.id == conv_id)
            .values(title=title, updated_at=func.current_timestamp())
        )
        await self._session.commit()

    async def delete_conversation(self, *, conv_id: str) -> None:
        # kb_chats punya ON DELETE CASCADE, jadi thread-nya cukup dihapus di induk.
        await self._session.execute(delete(KbConversation).where(KbConversation.id == conv_id))
        await self._session.commit()

    async def touch_conversation(self, *, conv_id: str) -> None:
        await self._session.execute(
            update(KbConversation)
            .where(KbConversation.id == conv_id)
            .values(updated_at=func.current_timestamp())
        )
        await self._session.commit()

    async def list_chats(self, *, conv_id: str) -> list[KbChat]:
        stmt = (
            select(KbChat).where(KbChat.conv_id == conv_id).order_by(KbChat.created_at, KbChat.id)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def history(self, *, conv_id: str, before_id: str, limit: int) -> list[dict[str, str]]:
        """Giliran sebelum `before_id`; batasnya posisi baris, bukan kecocokan teks."""
        stmt = text("""
            WITH anchor AS (
                SELECT created_at, id FROM kb_chats WHERE id = :before_id
            )
            SELECT c.role::text AS role, c.message
            FROM kb_chats c, anchor a
            WHERE c.conv_id = :conv_id AND c.message <> ''
              AND (c.status IS NULL OR c.status = 'done')
              AND (c.created_at, c.id) < (a.created_at, a.id)
            ORDER BY c.created_at DESC, c.id DESC
            LIMIT :limit
        """)
        result = await self._session.execute(
            stmt, {"conv_id": conv_id, "before_id": before_id, "limit": limit}
        )
        return list(reversed([dict(row) for row in result.mappings().all()]))

    async def create_chat(
        self, *, conv_id: str, role: str, message: str = "", status: str | None = None
    ) -> KbChat:
        chat = KbChat(conv_id=conv_id, role=role, message=message, status=status)
        self._session.add(chat)
        await self._session.flush()
        await self._session.commit()
        return chat

    async def finish_chat(
        self,
        *,
        chat_id: str,
        message: str,
        sources: list[dict[str, Any]] | None = None,
    ) -> None:
        await self._session.execute(
            update(KbChat)
            .where(KbChat.id == chat_id)
            .values(
                message=message,
                sources=sources,
                status=STATUS_DONE,
                error=None,
                updated_at=func.current_timestamp(),
            )
        )
        await self._session.commit()

    async def fail_chat(self, *, chat_id: str, error: str, message: str = "") -> None:
        await self._session.execute(
            update(KbChat)
            .where(KbChat.id == chat_id)
            .values(
                message=message,
                status=STATUS_FAILED,
                error=error[:500],
                updated_at=func.current_timestamp(),
            )
        )
        await self._session.commit()

    async def set_status(self, *, chat_id: str, status: str) -> None:
        await self._session.execute(
            update(KbChat)
            .where(KbChat.id == chat_id)
            .values(status=status, updated_at=func.current_timestamp())
        )
        await self._session.commit()

    async def abandon_streaming(self) -> int:
        """Job yang tergantung karena proses mati; dipanggil sekali saat startup."""
        result = await self._session.execute(
            update(KbChat)
            .where(KbChat.role == ROLE_ASSISTANT, KbChat.status.in_(("queued", "streaming")))
            .values(
                status=STATUS_FAILED,
                error="server restarted before the answer finished",
                updated_at=func.current_timestamp(),
            )
        )
        await self._session.commit()
        return int(result.rowcount or 0)


class RetrievalRepository:
    """Korpus RAG: percakapan WhatsApp tenant. Read-only dan selalu di-scope per tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_conversations(
        self, *, tenant_id: str, query: str, limit: int
    ) -> list[dict[str, Any]]:
        """Cari identitas percakapan + isi pesan; skor brand/nama lebih berat dari isi pesan."""
        stmt = text("""
            WITH pattern AS (
                SELECT '%' || :query || '%' AS like_query
            ),
            identity AS (
                SELECT v.id AS conv_id, 3 AS weight, NULL::text AS snippet, NULL::timestamptz AS at
                FROM wa_conversations v, pattern p
                WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
                  AND (v.brand_name ILIKE p.like_query
                       OR v.full_name ILIKE p.like_query
                       OR v.phone_number ILIKE p.like_query
                       OR v.note ILIKE p.like_query)
            ),
            body AS (
                SELECT c.conv_id, 1 AS weight,
                       LEFT(c.message, :snippet_chars) AS snippet,
                       c.created_at AS at
                FROM wa_chats c
                JOIN wa_conversations v ON v.id = c.conv_id, pattern p
                WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
                  AND c.message ILIKE p.like_query
            ),
            hits AS (
                SELECT * FROM identity
                UNION ALL
                SELECT * FROM body
            ),
            scored AS (
                SELECT conv_id,
                       SUM(weight) AS score,
                       COUNT(*) FILTER (WHERE snippet IS NOT NULL) AS message_hit_count,
                       MAX(at) AS last_hit_at,
                       (ARRAY_REMOVE(ARRAY_AGG(snippet ORDER BY at DESC NULLS LAST), NULL))[1:3]
                           AS snippets
                FROM hits
                GROUP BY conv_id
            )
            SELECT
                v.id AS conv_id,
                v.full_name,
                v.phone_number,
                v.brand_name,
                v.lead_status::text AS lead_status,
                v.project_value,
                v.note,
                s.score,
                s.message_hit_count,
                s.snippets,
                (SELECT MAX(c.created_at) FROM wa_chats c WHERE c.conv_id = v.id)
                    AS last_message_at
            FROM scored s
            JOIN wa_conversations v ON v.id = s.conv_id
            ORDER BY s.score DESC, last_message_at DESC NULLS LAST
            LIMIT :limit
        """)
        result = await self._session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "query": query,
                "limit": limit,
                "snippet_chars": SNIPPET_CHARS,
            },
        )
        return [dict(row) for row in result.mappings().all()]

    async def find_conversation(self, *, tenant_id: str, conv_id: str) -> dict[str, Any] | None:
        stmt = text("""
            SELECT id AS conv_id, full_name, phone_number, brand_name,
                   lead_status::text AS lead_status, project_value, winning_rate,
                   mode::text AS mode, note, created_at
            FROM wa_conversations
            WHERE id = :conv_id AND tenant_id = :tenant_id AND NOT is_internal
        """)
        result = await self._session.execute(stmt, {"conv_id": conv_id, "tenant_id": tenant_id})
        row = result.mappings().first()
        return dict(row) if row else None

    async def transcript(self, *, conv_id: str, limit: int) -> list[dict[str, Any]]:
        """Ambil dari yang terbaru supaya percakapan panjang terpotong di ujung lama, bukan baru."""
        stmt = text("""
            SELECT direction::text AS direction, type::text AS type,
                   LEFT(message, :max_chars) AS message, created_at
            FROM wa_chats
            WHERE conv_id = :conv_id
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
        """)
        result = await self._session.execute(
            stmt,
            {
                "conv_id": conv_id,
                "limit": min(limit, MAX_TRANSCRIPT_CHATS),
                "max_chars": MAX_TRANSCRIPT_MESSAGE_CHARS,
            },
        )
        return list(reversed([dict(row) for row in result.mappings().all()]))

    async def latest_activity(self, *, tenant_id: str) -> datetime | None:
        stmt = text("""
            SELECT MAX(c.created_at)
            FROM wa_chats c
            JOIN wa_conversations v ON v.id = c.conv_id
            WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
        """)
        return (await self._session.execute(stmt, {"tenant_id": tenant_id})).scalar_one_or_none()
