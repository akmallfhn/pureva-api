"""Query agregat untuk dashboard. Semuanya read-only dan di-scope per tenant."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Satu "turn" = pesan masuk yang membuka giliran balas, yaitu inbound pertama setelah outbound
# terakhir. Deteksinya harus melihat seluruh riwayat percakapan, jadi filter tanggal baru
# diterapkan di `resolved`, bukan di `scoped`.
TURNS_CTE = """
    scoped AS (
        SELECT c.id, c.conv_id, c.direction, c.created_at
        FROM wa_chats c
        JOIN wa_conversations v ON v.id = c.conv_id
        WHERE v.tenant_id = :tenant_id
    ),
    ordered AS (
        SELECT conv_id, direction, created_at,
               LAG(direction) OVER (PARTITION BY conv_id ORDER BY created_at, id) AS prev_dir
        FROM scoped
    ),
    turns AS (
        SELECT conv_id, created_at AS inbound_at
        FROM ordered
        WHERE direction = 'inbound' AND prev_dir IS DISTINCT FROM 'inbound'
    ),
    resolved AS (
        SELECT t.conv_id, t.inbound_at,
               (SELECT MIN(o.created_at) FROM wa_chats o
                WHERE o.conv_id = t.conv_id
                  AND o.direction = 'outbound'
                  AND o.created_at > t.inbound_at) AS replied_at
        FROM turns t
        WHERE t.inbound_at >= :start_at AND t.inbound_at < :end_at
    )
"""

# Pesan terakhir tiap percakapan yang aktivitas terakhirnya jatuh di rentang yang diminta.
_LAST_MESSAGE_CTE = """
    last_message AS (
        SELECT DISTINCT ON (c.conv_id)
            c.conv_id, c.direction, c.created_at, c.type, c.message
        FROM wa_chats c
        JOIN wa_conversations v ON v.id = c.conv_id
        WHERE v.tenant_id = :tenant_id
          AND c.created_at >= :start_at AND c.created_at < :end_at
        ORDER BY c.conv_id, c.created_at DESC, c.id DESC
    )
"""

_RESPONSE_AGGREGATES = """
    COUNT(*) AS inbound_turn_count,
    COUNT(replied_at) AS replied_turn_count,
    COUNT(*) - COUNT(replied_at) AS unanswered_turn_count,
    ROUND(EXTRACT(EPOCH FROM PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY replied_at - inbound_at) FILTER (WHERE replied_at IS NOT NULL)))::bigint
        AS median_response_seconds,
    ROUND(EXTRACT(EPOCH FROM PERCENTILE_CONT(0.9) WITHIN GROUP (
        ORDER BY replied_at - inbound_at) FILTER (WHERE replied_at IS NOT NULL)))::bigint
        AS p90_response_seconds,
    COUNT(*) FILTER (
        WHERE replied_at IS NOT NULL
          AND replied_at - inbound_at <= make_interval(secs => :target_seconds)
    ) AS within_target_count
"""


def _rows(result) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


class StatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def summary(
        self, *, tenant_id: str, start_at: datetime, end_at: datetime, target_seconds: int
    ) -> dict[str, Any]:
        stmt = text(f"""
            WITH {TURNS_CTE},
            active AS (
                SELECT DISTINCT conv_id FROM scoped
                WHERE direction = 'inbound' AND created_at >= :start_at AND created_at < :end_at
            ),
            fresh AS (
                SELECT id FROM wa_conversations
                WHERE tenant_id = :tenant_id AND created_at >= :start_at AND created_at < :end_at
            )
            SELECT
                (SELECT COUNT(*) FROM active) AS active_conversation_count,
                (SELECT COUNT(*) FROM fresh) AS new_conversation_count,
                COUNT(DISTINCT conv_id) FILTER (WHERE replied_at IS NULL)
                    AS unanswered_conversation_count,
                {_RESPONSE_AGGREGATES}
            FROM resolved
        """)
        result = await self._session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "start_at": start_at,
                "end_at": end_at,
                "target_seconds": target_seconds,
            },
        )
        return dict(result.mappings().one())

    async def volume_per_day(
        self, *, tenant_id: str, start_at: datetime, end_at: datetime, tz: str
    ) -> list[dict[str, Any]]:
        stmt = text("""
            WITH days AS (
                SELECT generate_series(
                    (:start_at AT TIME ZONE :tz)::date,
                    ((:end_at AT TIME ZONE :tz) - INTERVAL '1 microsecond')::date,
                    INTERVAL '1 day'
                )::date AS bucket
            ),
            inbound AS (
                SELECT DISTINCT
                    (c.created_at AT TIME ZONE :tz)::date AS bucket,
                    c.conv_id,
                    (v.created_at AT TIME ZONE :tz)::date AS conversation_started_on
                FROM wa_chats c
                JOIN wa_conversations v ON v.id = c.conv_id
                WHERE v.tenant_id = :tenant_id
                  AND c.direction = 'inbound'
                  AND c.created_at >= :start_at AND c.created_at < :end_at
            )
            SELECT
                days.bucket AS date,
                COUNT(i.conv_id) AS conversation_count,
                COUNT(i.conv_id) FILTER (WHERE i.conversation_started_on = days.bucket)
                    AS new_conversation_count,
                COUNT(i.conv_id) FILTER (WHERE i.conversation_started_on < days.bucket)
                    AS returning_conversation_count
            FROM days
            LEFT JOIN inbound i ON i.bucket = days.bucket
            GROUP BY days.bucket
            ORDER BY days.bucket
        """)
        result = await self._session.execute(
            stmt, {"tenant_id": tenant_id, "start_at": start_at, "end_at": end_at, "tz": tz}
        )
        return _rows(result)

    async def response_time_per_day(
        self,
        *,
        tenant_id: str,
        start_at: datetime,
        end_at: datetime,
        tz: str,
        target_seconds: int,
    ) -> list[dict[str, Any]]:
        # Hari tanpa pesan masuk tetap dikembalikan (median null) supaya line chart tidak putus.
        stmt = text(f"""
            WITH {TURNS_CTE},
            days AS (
                SELECT generate_series(
                    (:start_at AT TIME ZONE :tz)::date,
                    ((:end_at AT TIME ZONE :tz) - INTERVAL '1 microsecond')::date,
                    INTERVAL '1 day'
                )::date AS bucket
            ),
            per_day AS (
                SELECT (inbound_at AT TIME ZONE :tz)::date AS bucket, inbound_at, replied_at
                FROM resolved
            )
            SELECT
                days.bucket AS date,
                {_RESPONSE_AGGREGATES.replace("COUNT(*)", "COUNT(p.inbound_at)")}
            FROM days
            LEFT JOIN per_day p ON p.bucket = days.bucket
            GROUP BY days.bucket
            ORDER BY days.bucket
        """)
        result = await self._session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "start_at": start_at,
                "end_at": end_at,
                "tz": tz,
                "target_seconds": target_seconds,
            },
        )
        return _rows(result)

    async def inbound_heatmap(
        self, *, tenant_id: str, start_at: datetime, end_at: datetime, tz: str
    ) -> list[dict[str, Any]]:
        stmt = text("""
            SELECT
                EXTRACT(ISODOW FROM c.created_at AT TIME ZONE :tz)::int AS day_of_week,
                EXTRACT(HOUR FROM c.created_at AT TIME ZONE :tz)::int AS hour,
                COUNT(*) AS message_count,
                COUNT(DISTINCT c.conv_id) AS conversation_count
            FROM wa_chats c
            JOIN wa_conversations v ON v.id = c.conv_id
            WHERE v.tenant_id = :tenant_id
              AND c.direction = 'inbound'
              AND c.created_at >= :start_at AND c.created_at < :end_at
            GROUP BY 1, 2
            ORDER BY 1, 2
        """)
        result = await self._session.execute(
            stmt, {"tenant_id": tenant_id, "start_at": start_at, "end_at": end_at, "tz": tz}
        )
        return _rows(result)

    async def lead_status(
        self, *, tenant_id: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, Any]]:
        # Stage tanpa percakapan tetap dikembalikan (hitungan nol) supaya funnel tidak bolong.
        stmt = text("""
            WITH stages AS (
                SELECT unnest(enum_range(NULL::wa_lead_status_enum)) AS lead_status
            ),
            convs AS (
                SELECT lead_status, mode, winning_rate, project_value
                FROM wa_conversations
                WHERE tenant_id = :tenant_id
                  AND created_at >= :start_at AND created_at < :end_at
            )
            SELECT
                s.lead_status,
                COUNT(c.lead_status) AS conversation_count,
                COUNT(*) FILTER (WHERE c.mode = 'ai') AS mode_ai_count,
                COUNT(*) FILTER (WHERE c.mode = 'human') AS mode_human_count,
                COALESCE(ROUND(AVG(c.winning_rate))::int, 0) AS avg_winning_rate,
                COUNT(c.project_value) AS valued_conversation_count,
                COALESCE(SUM(c.project_value), 0)::bigint AS total_project_value
            FROM stages s
            LEFT JOIN convs c ON c.lead_status = s.lead_status
            GROUP BY s.lead_status
            ORDER BY s.lead_status
        """)
        result = await self._session.execute(
            stmt, {"tenant_id": tenant_id, "start_at": start_at, "end_at": end_at}
        )
        return _rows(result)

    async def unanswered(
        self, *, tenant_id: str, start_at: datetime, end_at: datetime, limit: int, skip: int
    ) -> list[dict[str, Any]]:
        stmt = text(f"""
            WITH {TURNS_CTE}
            SELECT
                v.id AS conv_id,
                v.full_name,
                v.phone_number,
                v.brand_name,
                v.lead_status,
                v.project_value,
                v.note,
                COUNT(*) AS unanswered_turn_count,
                MIN(r.inbound_at) AS first_unanswered_at,
                MAX(r.inbound_at) AS last_unanswered_at,
                ROUND(EXTRACT(EPOCH FROM (NOW() - MIN(r.inbound_at))) / 3600)::int AS waiting_hours
            FROM resolved r
            JOIN wa_conversations v ON v.id = r.conv_id
            WHERE r.replied_at IS NULL
            GROUP BY v.id, v.full_name, v.phone_number, v.brand_name,
                     v.lead_status, v.project_value, v.note
            ORDER BY MIN(r.inbound_at)
            LIMIT :limit OFFSET :skip
        """)
        result = await self._session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "start_at": start_at,
                "end_at": end_at,
                "limit": limit,
                "skip": skip,
            },
        )
        return _rows(result)

    async def count_unanswered(
        self, *, tenant_id: str, start_at: datetime, end_at: datetime
    ) -> int:
        stmt = text(f"""
            WITH {TURNS_CTE}
            SELECT COUNT(DISTINCT conv_id) FROM resolved WHERE replied_at IS NULL
        """)
        result = await self._session.execute(
            stmt, {"tenant_id": tenant_id, "start_at": start_at, "end_at": end_at}
        )
        return int(result.scalar_one())

    async def needs_action(
        self,
        *,
        tenant_id: str,
        start_at: datetime,
        end_at: datetime,
        idle_hours: int,
        limit: int,
        skip: int,
    ) -> list[dict[str, Any]]:
        stmt = text(f"""
            WITH {_LAST_MESSAGE_CTE}
            SELECT
                v.id AS conv_id,
                v.full_name,
                v.phone_number,
                v.brand_name,
                v.lead_status,
                v.project_value,
                v.winning_rate,
                v.mode,
                v.note,
                lm.created_at AS last_message_at,
                lm.type AS last_message_type,
                LEFT(lm.message, 120) AS last_message_preview,
                ROUND(EXTRACT(EPOCH FROM (NOW() - lm.created_at)) / 3600)::int AS idle_hours
            FROM last_message lm
            JOIN wa_conversations v ON v.id = lm.conv_id
            WHERE lm.direction = 'inbound'
              AND lm.created_at < NOW() - make_interval(hours => :idle_hours)
            ORDER BY lm.created_at
            LIMIT :limit OFFSET :skip
        """)
        result = await self._session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "start_at": start_at,
                "end_at": end_at,
                "idle_hours": idle_hours,
                "limit": limit,
                "skip": skip,
            },
        )
        return _rows(result)

    async def count_needs_action(
        self, *, tenant_id: str, start_at: datetime, end_at: datetime, idle_hours: int
    ) -> int:
        stmt = text(f"""
            WITH {_LAST_MESSAGE_CTE}
            SELECT COUNT(*) FROM last_message
            WHERE direction = 'inbound'
              AND created_at < NOW() - make_interval(hours => :idle_hours)
        """)
        result = await self._session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "start_at": start_at,
                "end_at": end_at,
                "idle_hours": idle_hours,
            },
        )
        return int(result.scalar_one())
