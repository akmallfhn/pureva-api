from datetime import date

from pydantic import BaseModel, Field


class StatRequest(BaseModel):
    """Field dasar yang dipakai semua endpoint statistik."""

    tenant_id: str
    start_date: date | None = None
    end_date: date | None = None
    timezone: str | None = None


class TargetRequest(StatRequest):
    """Field untuk endpoint yang mengukur first response terhadap sebuah target."""

    # Target first response dari dokumen evaluasi: 15 menit.
    target_seconds: int = Field(default=900, ge=1, le=86_400)


class ResponseTimeRequest(TargetRequest):
    # Sabtu–Minggu dibuang dari seri kalau tim memang tidak berjaga di akhir pekan.
    exclude_weekend: bool = False


class SummaryRequest(TargetRequest):
    pass


class ListRequest(StatRequest):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1)


class BrandListRequest(BaseModel):
    """Daftar brand deal: tidak difilter rentang tanggal, cukup tenant dan paging."""

    tenant_id: str
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1)
