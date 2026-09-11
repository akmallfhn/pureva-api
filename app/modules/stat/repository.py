"""Query agregat untuk dashboard. Semuanya read-only dan di-scope per tenant."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.stat.schema import ResponseMode

# Satu "turn" = pesan masuk yang membuka giliran balas, yaitu inbound pertama setelah outbound
# terakhir. Deteksinya harus melihat seluruh riwayat percakapan, jadi filter tanggal baru
# diterapkan di `resolved`, bukan di `scoped`.
TURNS_CTE = """
    scoped AS (
        SELECT c.id, c.conv_id, c.direction, c.created_at
        FROM wa_chats c
        JOIN wa_conversations v ON v.id = c.conv_id
        WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
    ),
    ordered AS (
        SELECT id, conv_id, direction, created_at,
               LAG(direction) OVER (PARTITION BY conv_id ORDER BY created_at, id) AS prev_dir
        FROM scoped
    ),
    turns AS (
        SELECT conv_id, created_at AS inbound_at,
               ROW_NUMBER() OVER (PARTITION BY conv_id ORDER BY created_at, id) AS turn_seq
        FROM ordered
        WHERE direction = 'inbound' AND prev_dir IS DISTINCT FROM 'inbound'
    ),
    resolved AS (
        SELECT t.conv_id, t.inbound_at, t.turn_seq,
               (SELECT MIN(o.created_at) FROM wa_chats o
                WHERE o.conv_id = t.conv_id
                  AND o.direction = 'outbound'
                  AND o.created_at > t.inbound_at) AS replied_at
        FROM turns t
        WHERE t.inbound_at >= :start_at AND t.inbound_at < :end_at
    )
"""

# Pesan terakhir tiap percakapan, dilihat dari seluruh riwayat supaya deal lama tetap terbaca.
_LAST_MESSAGE_CTE = """
    last_message AS (
        SELECT DISTINCT ON (c.conv_id)
            c.conv_id, c.direction, c.created_at, c.type, c.message
        FROM wa_chats c
        JOIN wa_conversations v ON v.id = c.conv_id
        WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
        ORDER BY c.conv_id, c.created_at DESC, c.id DESC
    )
"""

# Jam kerja untuk mode all_working, dibaca pada timezone yang diminta: Senin-Jumat 09.00-18.00.
WORK_START_SECONDS = 9 * 3600
WORK_END_SECONDS = 18 * 3600
WORK_DAY_SECONDS = WORK_END_SECONDS - WORK_START_SECONDS
# Titik nol hari kerja kumulatif; harus Senin supaya sisa bagi 7 langsung jadi indeks hari.
WORK_EPOCH = "DATE '2000-01-03'"


def _work_seconds_since_epoch(ts: str) -> str:
    """Detik jam kerja dari WORK_EPOCH sampai `ts`, sebuah timestamp tanpa timezone."""
    day = f"({ts})::date"
    days = f"({day} - {WORK_EPOCH})"
    return f"""(
                    (({days} / 7) * 5 + LEAST(MOD({days}, 7), 5)) * {WORK_DAY_SECONDS}
                    + CASE WHEN EXTRACT(ISODOW FROM {day}) < 6
                           THEN LEAST(GREATEST(
                                    EXTRACT(EPOCH FROM ({ts})::time) - {WORK_START_SECONDS}, 0),
                                {WORK_DAY_SECONDS})
                           ELSE 0 END
                )"""


def _response_seconds(mode: ResponseMode) -> str:
    """Lama balas satu turn; di mode all_working jeda di luar jam kerja tidak ikut dihitung."""
    if mode is not ResponseMode.ALL_WORKING:
        return "EXTRACT(EPOCH FROM replied_at - inbound_at)"
    reply = _work_seconds_since_epoch("replied_at AT TIME ZONE :tz")
    inbound = _work_seconds_since_epoch("inbound_at AT TIME ZONE :tz")
    return f"({reply} - {inbound})"


def _measured_cte(mode: ResponseMode) -> str:
    """Turn yang masuk hitungan response time, sudah dilengkapi lama balasnya dalam detik."""
    return f"""
    measured AS (
        SELECT conv_id, inbound_at, replied_at,
               {_response_seconds(mode)} AS response_seconds
        FROM resolved
        WHERE NOT CAST(:first_turn_only AS boolean) OR turn_seq = 1
    )
"""


_RESPONSE_AGGREGATES = """
    COUNT(*) AS inbound_turn_count,
    COUNT(response_seconds) AS replied_turn_count,
    COUNT(*) - COUNT(response_seconds) AS unanswered_turn_count,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY response_seconds::double precision)
        FILTER (WHERE response_seconds IS NOT NULL))::bigint AS median_response_seconds,
    ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY response_seconds::double precision)
        FILTER (WHERE response_seconds IS NOT NULL))::bigint AS p90_response_seconds,
    COUNT(*) FILTER (WHERE response_seconds <= :target_seconds) AS within_target_count
"""


def _rows(result) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


class StatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def summary(
        self,
        *,
        tenant_id: str,
        start_at: datetime,
        end_at: datetime,
        tz: str,
        target_seconds: int,
        mode: ResponseMode,
    ) -> dict[str, Any]:
        stmt = text(f"""
            WITH {TURNS_CTE},
            {_measured_cte(mode)},
            active AS (
                SELECT DISTINCT conv_id FROM scoped
                WHERE direction = 'inbound' AND created_at >= :start_at AND created_at < :end_at
            ),
            fresh AS (
                SELECT id FROM wa_conversations
                WHERE tenant_id = :tenant_id AND NOT is_internal
                  AND created_at >= :start_at AND created_at < :end_at
            )
            SELECT
                (SELECT COUNT(*) FROM active) AS active_conversation_count,
                (SELECT COUNT(*) FROM fresh) AS new_conversation_count,
                COUNT(DISTINCT conv_id) FILTER (WHERE replied_at IS NULL)
                    AS unanswered_conversation_count,
                {_RESPONSE_AGGREGATES}
            FROM measured
        """)
        result = await self._session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "start_at": start_at,
                "end_at": end_at,
                "tz": tz,
                "target_seconds": target_seconds,
                "first_turn_only": mode is ResponseMode.FIRST,
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
                WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
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
        exclude_weekend: bool,
        mode: ResponseMode,
    ) -> list[dict[str, Any]]:
        # Hari tanpa pesan masuk tetap dikembalikan (median null) supaya line chart tidak putus.
        stmt = text(f"""
            WITH {TURNS_CTE},
            {_measured_cte(mode)},
            days AS (
                SELECT d::date AS bucket
                FROM generate_series(
                    (:start_at AT TIME ZONE :tz)::date,
                    ((:end_at AT TIME ZONE :tz) - INTERVAL '1 microsecond')::date,
                    INTERVAL '1 day'
                ) d
                WHERE NOT CAST(:exclude_weekend AS boolean) OR EXTRACT(ISODOW FROM d) < 6
            ),
            per_day AS (
                SELECT (inbound_at AT TIME ZONE :tz)::date AS bucket, inbound_at, response_seconds
                FROM measured
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
                "exclude_weekend": exclude_weekend,
                "first_turn_only": mode is ResponseMode.FIRST,
            },
        )
        return _rows(result)

    async def inbound_heatmap(
        self, *, tenant_id: str, start_at: datetime, end_at: datetime, tz: str
    ) -> list[dict[str, Any]]:
        # Satu bucket memuat pesan masuk dan keluar sekaligus, jadi jam yang sama sebanding.
        stmt = text("""
            SELECT
                EXTRACT(ISODOW FROM c.created_at AT TIME ZONE :tz)::int AS day_of_week,
                EXTRACT(HOUR FROM c.created_at AT TIME ZONE :tz)::int AS hour,
                COUNT(*) FILTER (WHERE c.direction = 'inbound') AS inbound_message_count,
                COUNT(*) FILTER (WHERE c.direction = 'outbound') AS outbound_message_count,
                COUNT(DISTINCT c.conv_id) FILTER (WHERE c.direction = 'inbound')
                    AS conversation_count
            FROM wa_chats c
            JOIN wa_conversations v ON v.id = c.conv_id
            WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
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
                WHERE tenant_id = :tenant_id AND NOT is_internal
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

    async def needs_action(self, *, tenant_id: str, limit: int, skip: int) -> list[dict[str, Any]]:
        # Semua percakapan yang brand_name-nya terisi, paling lama tidak ada aktivitas di atas.
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
                lm.direction AS last_message_direction,
                lm.type AS last_message_type,
                LEFT(lm.message, 120) AS last_message_preview,
                ROUND(EXTRACT(EPOCH FROM (NOW() - lm.created_at)) / 3600)::int AS idle_hours
            FROM wa_conversations v
            LEFT JOIN last_message lm ON lm.conv_id = v.id
            WHERE v.tenant_id = :tenant_id AND NOT v.is_internal
              AND v.brand_name IS NOT NULL
            ORDER BY lm.created_at
            LIMIT :limit OFFSET :skip
        """)
        result = await self._session.execute(
            stmt, {"tenant_id": tenant_id, "limit": limit, "skip": skip}
        )
        return _rows(result)

    async def count_needs_action(self, *, tenant_id: str) -> int:
        stmt = text("""
            SELECT COUNT(*) FROM wa_conversations
            WHERE tenant_id = :tenant_id AND NOT is_internal AND brand_name IS NOT NULL
        """)
        result = await self._session.execute(stmt, {"tenant_id": tenant_id})
        return int(result.scalar_one())
