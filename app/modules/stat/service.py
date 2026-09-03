"""Validasi input, rentang tanggal, dan perakitan angka turunan untuk dashboard."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.modules.stat.repository import StatRepository
from app.modules.stat.schema import (
    BrandListRequest,
    ListRequest,
    ResponseTimeRequest,
    StatRequest,
    SummaryRequest,
)
from app.modules.tenant.repository import TenantRepository
from app.shared import pagination
from app.shared.response import ApiError

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 366


class StatService:
    def __init__(self, *, stats: StatRepository, tenants: TenantRepository) -> None:
        self._stats = stats
        self._tenants = tenants

    async def _tenant(self, tenant_id: str) -> str:
        if not tenant_id.strip():
            raise ApiError(400, "tenant_id is required")
        if await self._tenants.find_by_id(tenant_id) is None:
            raise ApiError(404, "tenant not found")
        return tenant_id

    async def _scope(self, req: StatRequest) -> tuple[str, datetime, datetime, str]:
        """Validasi tenant + rentang tanggal, lalu ubah tanggal lokal jadi batas timestamptz."""
        await self._tenant(req.tenant_id)

        tz_name = req.timezone or settings.stat_timezone
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ApiError(400, "timezone must be a valid IANA timezone name") from None

        end_date = req.end_date or datetime.now(tz).date()
        start_date = req.start_date or end_date - timedelta(days=DEFAULT_RANGE_DAYS - 1)
        if start_date > end_date:
            raise ApiError(400, "start_date must be on or before end_date")
        if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
            raise ApiError(400, f"date range must not exceed {MAX_RANGE_DAYS} days")

        # end_date inklusif: batas atasnya awal hari berikutnya di zona waktu yang diminta.
        start_at = datetime.combine(start_date, datetime.min.time(), tzinfo=tz)
        end_at = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        return req.tenant_id, start_at, end_at, tz_name

    @staticmethod
    def _period(start_at: datetime, end_at: datetime, tz_name: str) -> dict[str, Any]:
        return {
            "start_date": start_at.date().isoformat(),
            "end_date": (end_at.date() - timedelta(days=1)).isoformat(),
            "timezone": tz_name,
        }

    async def summary(self, req: SummaryRequest) -> dict[str, Any]:
        tenant_id, start_at, end_at, tz_name = await self._scope(req)
        row = await self._stats.summary(
            tenant_id=tenant_id,
            start_at=start_at,
            end_at=end_at,
            target_seconds=req.target_seconds,
        )

        days = (end_at.date() - start_at.date()).days
        replied = row["replied_turn_count"]
        return {
            **self._period(start_at, end_at, tz_name),
            "target_seconds": req.target_seconds,
            "active_conversation_count": row["active_conversation_count"],
            "new_conversation_count": row["new_conversation_count"],
            "inbound_per_day": round(row["active_conversation_count"] / days, 2) if days else 0,
            "inbound_turn_count": row["inbound_turn_count"],
            "replied_turn_count": replied,
            "unanswered_turn_count": row["unanswered_turn_count"],
            "unanswered_conversation_count": row["unanswered_conversation_count"],
            "median_response_seconds": row["median_response_seconds"],
            "p90_response_seconds": row["p90_response_seconds"],
            "within_target_count": row["within_target_count"],
            "within_target_percent": _percent(row["within_target_count"], replied),
            "reply_rate_percent": _percent(replied, row["inbound_turn_count"]),
        }

    async def chats_volume(self, req: StatRequest) -> dict[str, Any]:
        tenant_id, start_at, end_at, tz_name = await self._scope(req)
        rows = await self._stats.volume_per_day(
            tenant_id=tenant_id, start_at=start_at, end_at=end_at, tz=tz_name
        )
        return {
            **self._period(start_at, end_at, tz_name),
            "total_conversation_count": sum(r["conversation_count"] for r in rows),
            "list": [{**r, "date": r["date"].isoformat()} for r in rows],
        }

    async def response_time(self, req: ResponseTimeRequest) -> dict[str, Any]:
        tenant_id, start_at, end_at, tz_name = await self._scope(req)
        rows = await self._stats.response_time_per_day(
            tenant_id=tenant_id,
            start_at=start_at,
            end_at=end_at,
            tz=tz_name,
            target_seconds=req.target_seconds,
        )
        return {
            **self._period(start_at, end_at, tz_name),
            "target_seconds": req.target_seconds,
            "list": [
                {
                    **r,
                    "date": r["date"].isoformat(),
                    "within_target_percent": _percent(
                        r["within_target_count"], r["replied_turn_count"]
                    ),
                }
                for r in rows
            ],
        }

    async def inbound_heatmap(self, req: StatRequest) -> dict[str, Any]:
        tenant_id, start_at, end_at, tz_name = await self._scope(req)
        rows = await self._stats.inbound_heatmap(
            tenant_id=tenant_id, start_at=start_at, end_at=end_at, tz=tz_name
        )
        return {
            **self._period(start_at, end_at, tz_name),
            "total_message_count": sum(r["message_count"] for r in rows),
            "list": rows,
        }

    async def lead_status(self, req: StatRequest) -> dict[str, Any]:
        tenant_id, start_at, end_at, tz_name = await self._scope(req)
        rows = await self._stats.lead_status(tenant_id=tenant_id, start_at=start_at, end_at=end_at)
        return {
            **self._period(start_at, end_at, tz_name),
            "total_conversation_count": sum(r["conversation_count"] for r in rows),
            "total_project_value": sum(r["total_project_value"] for r in rows),
            "list": rows,
        }

    async def unanswered(self, req: ListRequest) -> dict[str, Any]:
        tenant_id, start_at, end_at, tz_name = await self._scope(req)
        page, page_size = pagination.normalize(req.page, req.page_size)

        total = await self._stats.count_unanswered(
            tenant_id=tenant_id, start_at=start_at, end_at=end_at
        )
        rows = await self._stats.unanswered(
            tenant_id=tenant_id,
            start_at=start_at,
            end_at=end_at,
            limit=page_size,
            skip=pagination.offset(page, page_size),
        )
        return {
            **self._period(start_at, end_at, tz_name),
            "list": [_isoformat_times(r) for r in rows],
            "metapaging": pagination.meta(total, page, page_size),
        }

    async def needs_action(self, req: BrandListRequest) -> dict[str, Any]:
        tenant_id = await self._tenant(req.tenant_id)
        page, page_size = pagination.normalize(req.page, req.page_size)

        total = await self._stats.count_needs_action(tenant_id=tenant_id)
        rows = await self._stats.needs_action(
            tenant_id=tenant_id,
            limit=page_size,
            skip=pagination.offset(page, page_size),
        )
        return {
            "list": [_isoformat_times(r) for r in rows],
            "metapaging": pagination.meta(total, page, page_size),
        }


def _percent(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _isoformat_times(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if isinstance(v, datetime | date) else v for k, v in row.items()}
