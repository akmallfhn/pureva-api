from datetime import date

from pydantic import BaseModel, Field


class StatRequest(BaseModel):
    """Field dasar yang dipakai semua endpoint statistik."""

    tenant_id: str
    start_date: date | None = None
    end_date: date | None = None
    timezone: str | None = None


class ResponseTimeRequest(StatRequest):
    # Target first response dari dokumen evaluasi: 15 menit.
    target_seconds: int = Field(default=900, ge=1, le=86_400)


class SummaryRequest(ResponseTimeRequest):
    pass


class ListRequest(StatRequest):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1)


class BrandListRequest(BaseModel):
    """Daftar brand deal: tidak difilter rentang tanggal, cukup tenant dan paging."""

    tenant_id: str
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1)
