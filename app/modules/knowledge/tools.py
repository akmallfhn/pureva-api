"""Retriever agent Knowledge: tool agregat di atas StatService, bukan vector search."""

import json
import logging
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.modules.knowledge.repository import RetrievalRepository
from app.modules.stat.schema import (
    BrandListRequest,
    ListRequest,
    ResponseTimeRequest,
    StatRequest,
    SummaryRequest,
)
from app.modules.stat.service import StatService
from app.shared.response import ApiError

logger = logging.getLogger(__name__)

# Batas baris per tool call; menahan satu jawaban dari menelan seluruh context window.
MAX_ROWS = 25

_SPEAKER = {"inbound": "Brand", "outbound": "TRC"}


class GetSummary(BaseModel):
    """Ringkasan performa satu rentang tanggal: jumlah inbound, median dan p90 first response,
    turn tanpa balasan, dan persentase yang dibalas dalam target. Pakai ini lebih dulu untuk
    pertanyaan umum tentang kondisi atau performa."""

    start_date: date | None = Field(default=None, description="Awal rentang, YYYY-MM-DD.")
    end_date: date | None = Field(default=None, description="Akhir rentang, inklusif, YYYY-MM-DD.")
    target_seconds: int = Field(
        default=900, description="Target first response dalam detik. Default 900 (15 menit)."
    )


class GetDailyVolume(BaseModel):
    """Jumlah percakapan per hari, dipisah percakapan baru dan lanjutan. Pakai untuk pertanyaan
    tentang tren volume atau perbandingan antar periode."""

    start_date: date | None = Field(default=None, description="Awal rentang, YYYY-MM-DD.")
    end_date: date | None = Field(default=None, description="Akhir rentang, inklusif, YYYY-MM-DD.")


class GetResponseTime(BaseModel):
    """Median dan p90 first response per hari terhadap sebuah target. Pakai untuk pertanyaan
    tentang kecepatan balas dari hari ke hari."""

    start_date: date | None = Field(default=None, description="Awal rentang, YYYY-MM-DD.")
    end_date: date | None = Field(default=None, description="Akhir rentang, inklusif, YYYY-MM-DD.")
    target_seconds: int = Field(default=900, description="Target first response dalam detik.")
    exclude_weekend: bool = Field(
        default=False, description="True untuk membuang Sabtu dan Minggu dari seri."
    )


class GetInboundHeatmap(BaseModel):
    """Sebaran pesan masuk per jam dan per hari dalam minggu. Pakai untuk pertanyaan tentang
    kapan inbound ramai atau jam berapa yang perlu dijaga."""

    start_date: date | None = Field(default=None, description="Awal rentang, YYYY-MM-DD.")
    end_date: date | None = Field(default=None, description="Akhir rentang, inklusif, YYYY-MM-DD.")


class GetLeadStatus(BaseModel):
    """Jumlah percakapan per stage funnel (cold, qualified, rate_card_sent, negotiation, closed)
    beserta nilai project yang tercatat. Hanya menghitung percakapan yang dibuat pada rentang
    ini."""

    start_date: date | None = Field(default=None, description="Awal rentang, YYYY-MM-DD.")
    end_date: date | None = Field(default=None, description="Akhir rentang, inklusif, YYYY-MM-DD.")


class ListUnanswered(BaseModel):
    """Percakapan yang punya pesan masuk tanpa balasan sama sekali pada rentang ini, diurutkan
    dari yang paling lama menunggu. Pakai untuk pertanyaan tentang siapa yang belum dibalas."""

    start_date: date | None = Field(default=None, description="Awal rentang, YYYY-MM-DD.")
    end_date: date | None = Field(default=None, description="Akhir rentang, inklusif, YYYY-MM-DD.")
    limit: int = Field(default=10, description=f"Jumlah baris, maksimal {MAX_ROWS}.")


class ListBrandDeals(BaseModel):
    """Semua brand deal yang sedang berjalan — percakapan yang brand_name-nya sudah terisi —
    diurutkan dari yang paling lama tidak ada aktivitas. Daftar ini tidak difilter tanggal,
    jadi deal lama tetap ikut terbaca."""

    limit: int = Field(default=15, description=f"Jumlah baris, maksimal {MAX_ROWS}.")
    page: int = Field(default=1, description="Halaman, mulai dari 1.")


class SearchConversations(BaseModel):
    """Cari percakapan berdasarkan kata kunci pada nama brand, nama kontak, nomor, catatan, atau
    isi pesan. Pakai untuk pertanyaan yang menyebut nama brand tertentu atau topik tertentu
    ("siapa yang menawar harga", "deal dengan Tokopedia")."""

    query: str = Field(description="Kata kunci. Satu atau dua kata bekerja paling baik.")
    limit: int = Field(default=8, description=f"Jumlah percakapan, maksimal {MAX_ROWS}.")


class ReadConversation(BaseModel):
    """Baca transkrip satu percakapan WhatsApp. Panggil sesudah SearchConversations atau
    ListBrandDeals memberi conv_id, kalau butuh tahu isi pembicaraannya."""

    conv_id: str = Field(description="conv_id dari hasil tool sebelumnya.")
    limit: int = Field(default=60, description="Jumlah pesan terakhir yang dibaca.")


class ToolBox:
    """Tool retrieval yang sudah terikat ke satu tenant dan satu session database."""

    def __init__(
        self, *, tenant_id: str, stats: StatService, retrieval: RetrievalRepository
    ) -> None:
        self._tenant_id = tenant_id
        self._stats = stats
        self._retrieval = retrieval

    @staticmethod
    def definitions() -> list[type[BaseModel]]:
        return [
            GetSummary,
            GetDailyVolume,
            GetResponseTime,
            GetInboundHeatmap,
            GetLeadStatus,
            ListUnanswered,
            ListBrandDeals,
            SearchConversations,
            ReadConversation,
        ]

    async def run(self, name: str, args: dict[str, Any]) -> tuple[str, str]:
        """Jalankan satu tool. Mengembalikan (hasil untuk LLM, ringkasan satu baris untuk UI)."""
        handler = getattr(self, f"_{_snake(name)}", None)
        if handler is None:
            return f"tool tidak dikenal: {name}", "tool tidak dikenal"

        try:
            return await handler(args)
        except ApiError as e:
            # Kegagalan retrieval adalah bahan jawaban, bukan kegagalan percakapan.
            return f"gagal mengambil data: {e.message}", f"gagal: {e.message}"
        except Exception as e:
            logger.exception(f"knowledge: tool {name} failed")
            return f"gagal mengambil data: {e}", "gagal mengambil data"

    def _range(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "tenant_id": self._tenant_id,
            "start_date": args.get("start_date"),
            "end_date": args.get("end_date"),
        }

    async def _get_summary(self, args: dict[str, Any]) -> tuple[str, str]:
        data = await self._stats.summary(
            SummaryRequest(**self._range(args), target_seconds=args.get("target_seconds", 900))
        )
        return _dump(data), f"ringkasan {data['start_date']} sampai {data['end_date']}"

    async def _get_daily_volume(self, args: dict[str, Any]) -> tuple[str, str]:
        data = await self._stats.chats_volume(StatRequest(**self._range(args)))
        return _dump(data), f"volume harian {data['start_date']} sampai {data['end_date']}"

    async def _get_response_time(self, args: dict[str, Any]) -> tuple[str, str]:
        data = await self._stats.response_time(
            ResponseTimeRequest(
                **self._range(args),
                target_seconds=args.get("target_seconds", 900),
                exclude_weekend=args.get("exclude_weekend", False),
            )
        )
        return _dump(data), f"response time harian {data['start_date']} sampai {data['end_date']}"

    async def _get_inbound_heatmap(self, args: dict[str, Any]) -> tuple[str, str]:
        data = await self._stats.inbound_heatmap(StatRequest(**self._range(args)))
        # Sel bernilai nol dibuang: 168 sel penuh tidak menambah informasi apa pun ke prompt.
        data = {**data, "list": [r for r in data["list"] if r.get("inbound_message_count")]}
        return _dump(data), f"heatmap inbound {data['start_date']} sampai {data['end_date']}"

    async def _get_lead_status(self, args: dict[str, Any]) -> tuple[str, str]:
        data = await self._stats.lead_status(StatRequest(**self._range(args)))
        return _dump(data), f"funnel lead status {data['start_date']} sampai {data['end_date']}"

    async def _list_unanswered(self, args: dict[str, Any]) -> tuple[str, str]:
        size = _clamp(args.get("limit", 10))
        data = await self._stats.unanswered(ListRequest(**self._range(args), page_size=size))
        total = data["metapaging"]["total_data"]
        return _dump(data), f"{total} percakapan tanpa balasan"

    async def _list_brand_deals(self, args: dict[str, Any]) -> tuple[str, str]:
        size = _clamp(args.get("limit", 15))
        data = await self._stats.needs_action(
            BrandListRequest(
                tenant_id=self._tenant_id, page=max(int(args.get("page", 1)), 1), page_size=size
            )
        )
        total = data["metapaging"]["total_data"]
        return _dump(data), f"{total} brand deal berjalan"

    async def _search_conversations(self, args: dict[str, Any]) -> tuple[str, str]:
        query = str(args.get("query", "")).strip()
        if not query:
            return "query kosong", "query kosong"

        rows = await self._retrieval.search_conversations(
            tenant_id=self._tenant_id, query=query, limit=_clamp(args.get("limit", 8))
        )
        return _dump({"query": query, "list": rows}), f'cari "{query}" — {len(rows)} percakapan'

    async def _read_conversation(self, args: dict[str, Any]) -> tuple[str, str]:
        conv_id = str(args.get("conv_id", "")).strip()
        conv = await self._retrieval.find_conversation(tenant_id=self._tenant_id, conv_id=conv_id)
        if conv is None:
            return "percakapan tidak ditemukan", "percakapan tidak ditemukan"

        chats = await self._retrieval.transcript(conv_id=conv_id, limit=int(args.get("limit", 60)))
        transcript = "\n".join(_line(c) for c in chats)
        label = conv["brand_name"] or conv["full_name"]
        return _dump({**conv, "transcript": transcript}), f"transkrip {label} ({len(chats)} pesan)"


def _line(chat: dict[str, Any]) -> str:
    speaker = _SPEAKER.get(chat["direction"], chat["direction"])
    # Sticker/gambar/dokumen tidak punya teks; tipe pesannya saja sudah jadi konteks.
    body = (chat["message"] or "").strip() or f"[kiriman {chat['type']}]"
    return f"[{chat['created_at']:%Y-%m-%d %H:%M}] {speaker}: {body}"


def _clamp(value: Any) -> int:
    try:
        return max(1, min(int(value), MAX_ROWS))
    except (TypeError, ValueError):
        return 10


def _snake(name: str) -> str:
    out = [name[0].lower()]
    out += [f"_{c.lower()}" if c.isupper() else c for c in name[1:]]
    return "".join(out)


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
