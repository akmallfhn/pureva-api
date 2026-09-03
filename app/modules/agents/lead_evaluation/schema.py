"""Bentuk data evaluator lead: konteks percakapan, hasil penilaian LLM, dan state graph."""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

LeadStage = Literal["cold", "qualified", "rate_card_sent", "negotiation", "closed"]

# Urutan funnel; dipakai untuk memastikan stage hanya boleh maju, tidak mundur.
STAGE_ORDER: dict[str, int] = {
    "cold": 0,
    "qualified": 1,
    "rate_card_sent": 2,
    "negotiation": 3,
    "closed": 4,
}


class LeadEvaluation(BaseModel):
    """Penilaian LLM atas satu percakapan WhatsApp."""

    brand_name: str | None = Field(
        default=None,
        description=(
            "Nama brand, perusahaan, instansi, atau kepanitiaan yang mengajak kerja sama."
            " Tulis nama entitasnya saja, bukan nama orangnya."
            " null kalau belum bisa dipastikan dari percakapan."
        ),
    )
    project_value: int | None = Field(
        default=None,
        description=(
            "Nilai project dalam Rupiah penuh tanpa titik dan desimal, misal 15 juta -> 15000000."
            " Hanya isi kalau ada nominal yang benar-benar disebut di percakapan."
            " null kalau tidak ada nominal sama sekali."
        ),
    )
    lead_status: LeadStage = Field(
        description="Stage funnel deal berdasarkan bukti terakhir di percakapan.",
    )
    note: str | None = Field(
        default=None,
        description=(
            "Ringkasan 1-3 kalimat Bahasa Indonesia: siapa yang menghubungi, kebutuhannya apa,"
            " sudah sampai mana, dan apa langkah berikutnya."
        ),
    )


class ConversationContext(BaseModel):
    """Konteks yang diambil fetch_context sebelum percakapan dinilai."""

    conv_id: str
    full_name: str
    phone_number: str
    brand_name: str | None
    project_value: int | None
    lead_status: str
    note: str | None
    transcript: str
    chat_count: int
    text_count: int


class EvalState(TypedDict, total=False):
    conv_id: str
    context: ConversationContext | None
    verdict: LeadEvaluation | None
    changes: dict[str, Any]
    error: str | None
