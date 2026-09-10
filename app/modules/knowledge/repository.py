"""CRUD thread Knowledge, plus retriever leksikal ke percakapan WhatsApp yang jadi korpusnya."""

import json
import re
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

# Lebih lebar dari snippet jadinya; jendela di sekitar kata cocok dipotong di Python.
SNIPPET_SOURCE_CHARS = 1200
SNIPPET_COUNT = 3

# Default pg_trgm 0.6 kelewat ketat untuk bahasa campur; 0.45 masih menolak yang tak nyambung.
FUZZY_THRESHOLD = 0.45

# 0.45 menuntut similarity >= 0.53 pada nama brand; skor ini dikali 1.6 di peringkat akhir.
MIN_IDENTITY_SCORE = 0.45

# Kata pendek dilarang: trigram "nego" menabrak "Negeri", "harga" menabrak "Grand".
FUZZY_IDENTITY_MIN_CHARS = 5

# Menuntut similarity >= 0.6; di bawahnya word_similarity ke paragraf note selalu dapat.
MIN_NOTE_SCORE = 0.21

MIN_TERM_CHARS = 3
MAX_TERMS = 6

SPEAKER = {"inbound": "Brand", "outbound": "TRC"}

# "deal" dan "brand" ikut dibuang karena seluruh korpus ini memang brand deal.
_STOPWORDS = frozenset(
    {
        "yang",
        "yng",
        "dan",
        "atau",
        "dengan",
        "dgn",
        "untuk",
        "utk",
        "dari",
        "pada",
        "ada",
        "apa",
        "apakah",
        "siapa",
        "kapan",
        "kenapa",
        "mengapa",
        "mana",
        "dimana",
        "gimana",
        "bagaimana",
        "berapa",
        "ini",
        "itu",
        "aja",
        "saja",
        "sudah",
        "udah",
        "belum",
        "tidak",
        "gak",
        "nggak",
        "bukan",
        "buat",
        "nya",
        "yaa",
        "iya",
        "chat",
        "pesan",
        "percakapan",
        "deal",
        "brand",
        "client",
        "klien",
    }
)


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
    ) -> dict[str, Any]:
        """Dua jalur cocok: ILIKE eksak dan trigram fuzzy; skor dipecah per komponen di hasil."""
        terms = _terms(query)
        if not terms:
            return {"query": query, "terms": [], "total_found": 0, "list": []}

        # Operator <% supaya index GIN trgm kepakai; ambangnya transaction-local.
        await self._session.execute(
            text("SELECT set_config('pg_trgm.word_similarity_threshold', :value, true)"),
            {"value": str(FUZZY_THRESHOLD)},
        )

        stmt = text("""
            WITH terms AS (
                SELECT DISTINCT lower(t) AS term FROM unnest(CAST(:terms AS text[])) AS t
            ),
            scope AS (
                SELECT id, brand_name, full_name, phone_number, note
                FROM wa_conversations
                WHERE tenant_id = :tenant_id AND NOT is_internal
            ),
            -- Identitas = siapa percakapan ini: nama brand, nama kontak, nomor. Ini yang
            -- menjawab "cari deal Tokopedia", dan kecocokan di sini paling meyakinkan.
            identity_hits AS (
                SELECT
                    v.id AS conv_id,
                    t.term,
                    CASE
                        WHEN v.brand_name   ILIKE '%' || t.term || '%' THEN 1.00
                        WHEN v.full_name    ILIKE '%' || t.term || '%' THEN 0.90
                        WHEN v.phone_number ILIKE '%' || t.term || '%' THEN 0.85
                        WHEN LENGTH(t.term) < :fuzzy_min_chars THEN 0
                        ELSE GREATEST(
                            word_similarity(t.term, COALESCE(v.brand_name, '')) * 0.85,
                            word_similarity(t.term, COALESCE(v.full_name, ''))  * 0.75
                        )
                    END AS score,
                    CASE
                        WHEN v.brand_name   ILIKE '%' || t.term || '%' THEN 'brand_name'
                        WHEN v.full_name    ILIKE '%' || t.term || '%' THEN 'full_name'
                        WHEN v.phone_number ILIKE '%' || t.term || '%' THEN 'phone_number'
                        ELSE 'identity_fuzzy'
                    END AS field
                FROM scope v CROSS JOIN terms t
            ),
            -- note itu ringkasan naratif percakapan yang ditulis agent, bukan label pendek:
            -- isinya konten, bukan identitas. Dulu ia ikut blok identitas dengan bobot 0.70
            -- dan membuat tiap percakapan bertopik sama seri di skor yang persis sama.
            note_hits AS (
                SELECT
                    v.id AS conv_id,
                    t.term,
                    CASE WHEN v.note ILIKE '%' || t.term || '%' THEN 0.45
                         ELSE word_similarity(t.term, COALESCE(v.note, '')) * 0.35 END AS score
                FROM scope v CROSS JOIN terms t
            ),
            -- Kecocokan pada isi pesan. ILIKE dan <% dua-duanya index-assisted lewat index
            -- GIN trgm di wa_chats.message.
            body_hits AS (
                SELECT
                    c.conv_id, c.id AS chat_id, t.term,
                    c.direction::text AS direction,
                    c.created_at,
                    LEFT(c.message, :source_chars) AS message,
                    (c.message ILIKE '%' || t.term || '%') AS is_exact,
                    CASE WHEN c.message ILIKE '%' || t.term || '%' THEN 0.60
                         ELSE word_similarity(t.term, c.message) * 0.45 END AS score
                FROM wa_chats c
                JOIN scope v ON v.id = c.conv_id
                CROSS JOIN terms t
                WHERE c.message <> ''
                  AND (c.message ILIKE '%' || t.term || '%' OR t.term <% c.message)
            ),
            -- Satu pesan bisa kena beberapa term; simpan kecocokan terkuatnya saja supaya
            -- pesan yang memuat semua kata tidak dihitung berkali-kali.
            body_best AS (
                SELECT DISTINCT ON (chat_id) * FROM body_hits ORDER BY chat_id, score DESC
            ),
            identity_rolled AS (
                SELECT conv_id,
                       MAX(score) AS score,
                       ARRAY_AGG(DISTINCT term)  AS terms,
                       ARRAY_AGG(DISTINCT field) AS fields
                FROM identity_hits WHERE score >= :min_score GROUP BY conv_id
            ),
            note_rolled AS (
                SELECT conv_id,
                       MAX(score) AS score,
                       ARRAY_AGG(DISTINCT term) AS terms
                FROM note_hits WHERE score >= :min_note_score GROUP BY conv_id
            ),
            body_rolled AS (
                SELECT conv_id,
                       MAX(score) AS score,
                       COUNT(*) AS hit_count,
                       COUNT(*) FILTER (WHERE is_exact) AS exact_hit_count,
                       ARRAY_AGG(DISTINCT term) AS terms
                FROM body_best GROUP BY conv_id
            ),
            snippets AS (
                SELECT conv_id, JSON_AGG(JSON_BUILD_OBJECT(
                           'at', created_at, 'direction', direction,
                           'term', term, 'exact', is_exact, 'text', message
                       ) ORDER BY score DESC, created_at DESC) AS items
                FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY conv_id ORDER BY score DESC, created_at DESC
                    ) AS rn FROM body_best
                ) r
                WHERE rn <= :snippet_count
                GROUP BY conv_id
            ),
            merged AS (
                SELECT
                    COALESCE(i.conv_id, n.conv_id, b.conv_id) AS conv_id,
                    COALESCE(i.score, 0) AS identity_score,
                    COALESCE(n.score, 0) AS note_score,
                    COALESCE(b.score, 0) AS body_score,
                    COALESCE(b.hit_count, 0) AS message_hit_count,
                    COALESCE(b.exact_hit_count, 0) AS exact_message_hit_count,
                    COALESCE(i.fields, ARRAY[]::text[])
                        || CASE WHEN n.conv_id IS NULL THEN ARRAY[]::text[]
                                ELSE ARRAY['note'] END AS matched_fields,
                    ARRAY(SELECT DISTINCT unnest(
                        COALESCE(i.terms, ARRAY[]::text[])
                        || COALESCE(n.terms, ARRAY[]::text[])
                        || COALESCE(b.terms, ARRAY[]::text[])
                    )) AS matched_terms
                FROM identity_rolled i
                FULL OUTER JOIN note_rolled n ON n.conv_id = i.conv_id
                FULL OUTER JOIN body_rolled b
                    ON b.conv_id = COALESCE(i.conv_id, n.conv_id)
            )
            SELECT
                v.id AS conv_id, v.brand_name, v.full_name, v.phone_number,
                v.lead_status::text AS lead_status, v.project_value, v.winning_rate,
                v.mode::text AS mode, v.note,
                v.created_at AS conversation_started_at,
                ROUND(m.identity_score::numeric, 3) AS identity_score,
                ROUND(m.note_score::numeric, 3)     AS note_score,
                ROUND(m.body_score::numeric, 3)     AS body_score,
                m.message_hit_count,
                m.exact_message_hit_count,
                m.matched_fields,
                m.matched_terms,
                -- Identitas dikali 1.6 supaya percakapan yang memang milik brand itu tidak
                -- pernah kalah dari percakapan lain yang cuma menyebut namanya berkali-kali:
                -- identitas penuh 1.60 di atas plafon gabungan sisanya (0.45+0.60+0.32 = 1.37).
                -- Jumlah pesan yang kena dan cakupan term yang membedakan peringkat di
                -- pencarian topik, tempat tidak ada satu pun kecocokan identitas.
                ROUND((m.identity_score * 1.6
                       + m.note_score
                       + m.body_score
                       + LEAST(m.message_hit_count, 8) * 0.04
                       + CARDINALITY(m.matched_terms)::numeric
                         / GREATEST(CARDINALITY(CAST(:terms AS text[])), 1) * 0.35
                      )::numeric, 3) AS relevance,
                s.items AS snippets,
                st.inbound_count, st.outbound_count,
                st.first_message_at, st.last_message_at
            FROM merged m
            JOIN wa_conversations v ON v.id = m.conv_id
            LEFT JOIN snippets s ON s.conv_id = m.conv_id
            LEFT JOIN LATERAL (
                SELECT COUNT(*) FILTER (WHERE direction = 'inbound')  AS inbound_count,
                       COUNT(*) FILTER (WHERE direction = 'outbound') AS outbound_count,
                       MIN(created_at) AS first_message_at,
                       MAX(created_at) AS last_message_at
                FROM wa_chats WHERE conv_id = v.id
            ) st ON TRUE
            ORDER BY relevance DESC, st.last_message_at DESC NULLS LAST
            LIMIT :limit
        """)
        result = await self._session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "terms": terms,
                "limit": limit,
                "min_score": MIN_IDENTITY_SCORE,
                "min_note_score": MIN_NOTE_SCORE,
                "fuzzy_min_chars": FUZZY_IDENTITY_MIN_CHARS,
                "snippet_count": SNIPPET_COUNT,
                "source_chars": SNIPPET_SOURCE_CHARS,
            },
        )
        rows = [_shape_hit(dict(row)) for row in result.mappings().all()]
        return {"query": query, "terms": terms, "total_found": len(rows), "list": rows}

    async def nearest_identities(
        self, *, tenant_id: str, terms: list[str], limit: int
    ) -> list[dict[str, Any]]:
        """Nama termirip waktu pencarian nihil — bahan planner untuk menebak ejaan lalu ulang."""
        if not terms:
            return []

        stmt = text("""
            SELECT v.id AS conv_id, v.brand_name, v.full_name,
                   v.lead_status::text AS lead_status,
                   ROUND(MAX(GREATEST(
                       similarity(t.term, COALESCE(v.brand_name, '')),
                       similarity(t.term, COALESCE(v.full_name, ''))
                   ))::numeric, 3) AS closeness
            FROM wa_conversations v
            CROSS JOIN unnest(CAST(:terms AS text[])) AS t(term)
            WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
              AND (COALESCE(v.brand_name, '') <> '' OR COALESCE(v.full_name, '') <> '')
            GROUP BY v.id, v.brand_name, v.full_name, v.lead_status
            ORDER BY closeness DESC, v.brand_name
            LIMIT :limit
        """)
        result = await self._session.execute(
            stmt, {"tenant_id": tenant_id, "terms": terms, "limit": limit}
        )
        return [dict(row) for row in result.mappings().all()]

    async def find_conversation(self, *, tenant_id: str, conv_id: str) -> dict[str, Any] | None:
        stmt = text("""
            SELECT v.id AS conv_id, v.brand_name, v.full_name, v.phone_number,
                   v.lead_status::text AS lead_status, v.project_value, v.winning_rate,
                   v.mode::text AS mode, v.note,
                   v.created_at AS conversation_started_at,
                   st.inbound_count, st.outbound_count,
                   st.first_message_at, st.last_message_at
            FROM wa_conversations v
            LEFT JOIN LATERAL (
                SELECT COUNT(*) FILTER (WHERE direction = 'inbound')  AS inbound_count,
                       COUNT(*) FILTER (WHERE direction = 'outbound') AS outbound_count,
                       MIN(created_at) AS first_message_at,
                       MAX(created_at) AS last_message_at
                FROM wa_chats WHERE conv_id = v.id
            ) st ON TRUE
            WHERE v.id = :conv_id AND v.tenant_id = :tenant_id AND NOT v.is_internal
        """)
        result = await self._session.execute(stmt, {"conv_id": conv_id, "tenant_id": tenant_id})
        row = result.mappings().first()
        return dict(row) if row else None

    async def transcript(self, *, conv_id: str, limit: int) -> dict[str, Any]:
        """Diambil dari yang terbaru; total ikut supaya penjawab tahu transkripnya terpotong."""
        # COUNT(*) OVER () dievaluasi sebelum LIMIT, jadi angkanya total baris yang cocok.
        stmt = text("""
            SELECT direction::text AS direction, type::text AS type,
                   LEFT(message, :max_chars) AS message, created_at,
                   COUNT(*) OVER () AS total_message_count
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
        rows = [dict(row) for row in result.mappings().all()]
        total = int(rows[0]["total_message_count"]) if rows else 0
        for row in rows:
            row.pop("total_message_count", None)

        return {
            "total_message_count": total,
            "returned_count": len(rows),
            "truncated": total > len(rows),
            "list": list(reversed(rows)),
        }

    async def activity_window(self, *, tenant_id: str) -> tuple[datetime | None, datetime | None]:
        """Batas awal dan akhir korpus; yang awal menahan perbandingan lari ke luar data."""
        stmt = text("""
            SELECT MIN(c.created_at) AS earliest, MAX(c.created_at) AS latest
            FROM wa_chats c
            JOIN wa_conversations v ON v.id = c.conv_id
            WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
        """)
        row = (await self._session.execute(stmt, {"tenant_id": tenant_id})).mappings().one()
        return row["earliest"], row["latest"]


def _terms(query: str) -> list[str]:
    """Pecah query jadi token pencarian, buang kata yang cocok ke mana-mana."""
    raw = re.findall(r"[0-9a-zA-ZÀ-ɏ]+", query.lower())
    kept = [t for t in raw if len(t) >= MIN_TERM_CHARS and t not in _STOPWORDS]
    # Query yang isinya stopword semua tetap harus mencari sesuatu, bukan mengembalikan nihil.
    if not kept:
        kept = [t for t in raw if len(t) >= 2]
    return list(dict.fromkeys(kept))[:MAX_TERMS]


def _window(message: str, term: str, width: int) -> str:
    """Potong pesan panjang di sekitar kata yang cocok, bukan dari awal pesan."""
    body = (message or "").strip()
    if len(body) <= width:
        return body

    at = body.lower().find(term.lower())
    if at < 0:
        return body[:width].rstrip() + "…"

    start = max(0, at - width // 3)
    end = min(len(body), start + width)
    return ("…" if start > 0 else "") + body[start:end].strip() + ("…" if end < len(body) else "")


def _shape_hit(row: dict[str, Any]) -> dict[str, Any]:
    """Rapikan satu baris hasil pencarian jadi bentuk yang enak dibaca penjawab."""
    # asyncpg mengembalikan json sebagai string; ORM tidak ikut campur di query text() mentah.
    raw = row.get("snippets")
    if isinstance(raw, str):
        raw = json.loads(raw)

    row["snippets"] = [
        {
            "at": s["at"],
            "speaker": SPEAKER.get(s["direction"], s["direction"]),
            "exact": s["exact"],
            "text": _window(s["text"], s["term"], SNIPPET_CHARS),
        }
        for s in (raw or [])
    ]
    return row
