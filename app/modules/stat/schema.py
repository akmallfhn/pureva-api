from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class ResponseMode(StrEnum):
    """Turn mana yang diukur, dan apakah jeda di luar jam kerja ikut dihitung."""

    # Hanya turn pembuka tiap percakapan, jeda apa adanya.
    FIRST = "first"
    # Semua turn, jeda dipotong ke jam kerja Senin-Jumat 09.00-18.00.
    ALL_WORKING = "all_working"
    # Semua turn, jeda apa adanya.
    ALL_FLAT = "all_flat"


class StatRequest(BaseModel):
    """Field dasar yang dipakai semua endpoint statistik."""

    tenant_id: str
    start_date: date | None = None
    end_date: date | None = None
    timezone: str | None = None


class TargetRequest(StatRequest):
    """Field untuk endpoint yang mengukur response time terhadap sebuah target."""

    # Target first response dari dokumen evaluasi: 15 menit.
    target_seconds: int = Field(default=900, ge=1, le=86_400)
    # Default mempertahankan angka lama: semua turn, tanpa potongan jam kerja.
    response_mode: ResponseMode = ResponseMode.ALL_FLAT


class ResponseTimeRequest(TargetRequest):
    # Sabtu-Minggu dibuang dari seri kalau tim memang tidak berjaga di akhir pekan.
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
