"""Helper murni (tanpa I/O) untuk format context & routing graph Pureva."""

import logging
import re

logger = logging.getLogger(__name__)

# Keyword sederhana per kategori keluhan -> dipakai untuk menyaring katalog
# sebelum dikirim ke node skin assessment (hemat token & lebih relevan).
CONCERN_KEYWORDS = {
    "jerawat": ["acne", "jerawat", "ipl", "blue light", "peeling", "perfeclean"],
    "bekas": ["scar", "bopeng", "bekas", "microneedle", "rf", "pico", "fractional", "co2"],
    "flek": ["pigment", "melasma", "flek", "dark spot", "brightening", "laser", "peeling"],
    "kusam": ["glow", "brightening", "facial", "peeling", "infusion", "radiance", "laser"],
    "kerut": ["aging", "filler", "botulinum", "microtox", "booster", "tightening", "hifu", "ulthera"],
    "penuaan": ["aging", "filler", "booster", "tightening", "hifu", "collagen", "profhilo"],
    "kendur": ["tightening", "hifu", "rf", "ultraformer", "ulthera", "threadlift", "volnewmer"],
    "pori": ["pore", "pori", "microneedle", "rf", "facial", "perfeclean"],
    "rambut": ["hair", "rambut", "hgat", "dermapen", "onsen", "scalp", "dandruff"],
    "rontok": ["hair", "rambut", "hgat", "prp", "anti hair fall", "red light"],
    "sensitif": ["sensitive", "sensitif", "barrier", "recovery", "soothing", "hyaluronic"],
    "iritasi": ["sensitive", "barrier", "recovery", "soothing", "calming"],
    "komedo": ["comedo", "komedo", "perfeclean", "facial", "extraction", "milia"],
    "lemak": ["fat", "lemak", "sculpting", "dissolving", "mesotherapy", "contour"],
}


def derive_keywords(message: str) -> list[str]:
    """Tarik keyword pencarian katalog dari pesan pasien."""
    text = (message or "").lower()
    keywords: list[str] = []
    for trigger, mapped in CONCERN_KEYWORDS.items():
        if trigger in text:
            keywords.extend(mapped)
    # fallback: pakai kata-kata penting dari pesan
    if not keywords:
        keywords = [w for w in re.findall(r"[a-zA-Z]{4,}", text)][:6]
    # de-dup pertahankan urutan
    seen, out = set(), []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def format_treatments(treatments: list[dict], limit: int = 20) -> str:
    if not treatments:
        return "(tidak ada data treatment)"
    lines = []
    for t in treatments[:limit]:
        lines.append(
            f"- {t.get('name', '')} | {t.get('category', '')} | mulai {t.get('price_text', '')} | "
            f"{(t.get('description') or '')[:140]}"
        )
    return "\n".join(lines)


def format_doctors(doctors: list[dict]) -> str:
    if not doctors:
        return "(tidak ada data jadwal dokter)"
    lines = []
    for d in doctors:
        lines.append(f"- {d.get('name', '')}: {d.get('day', '')} {d.get('hours', '')}")
    return "\n".join(lines)


def format_discount(discount: dict | None) -> str:
    if not discount:
        return "Tidak ada promo khusus hari ini."
    return (
        f"{discount.get('promo_name', '')} - diskon {discount.get('discount_percent', 0)}% "
        f"untuk {discount.get('applies_to', '')}"
    )


def format_all_discounts(discounts: list[dict]) -> str:
    if not discounts:
        return "(tidak ada data promo)"
    order = {"Senin": 0, "Selasa": 1, "Rabu": 2, "Kamis": 3, "Jumat": 4, "Sabtu": 5, "Minggu": 6}
    rows = sorted(discounts, key=lambda d: order.get(d.get("day", ""), 99))
    return "\n".join(
        f"- {d.get('day', '')}: {d.get('promo_name', '')} ({d.get('discount_percent', 0)}%)"
        for d in rows
    )


def format_history(history: list[dict], limit: int = 8) -> str:
    if not history:
        return "(belum ada percakapan sebelumnya)"
    lines = []
    for h in history[-limit:]:
        who = "Pasien" if h.get("role") == "user" else "Vera"
        lines.append(f"{who}: {h.get('content', '')}")
    return "\n".join(lines)


def split_bubbles(text: str) -> list[str]:
    bubbles = [b.strip() for b in (text or "").split("||") if b.strip()]
    if not bubbles:
        bubbles = [(text or "").strip() or "Maaf, ada gangguan sebentar. Boleh diulang ya 🙏"]
    return bubbles[:2]


def route_by_intent(state: dict) -> str:
    intent = state.get("intent", "general_info")
    mapping = {
        "skin_consult": "skin_assessment",
        "booking": "booking",
        "complaint": "complaint",
        "general_info": "general_info",
    }
    next_node = mapping.get(intent, "general_info")
    logger.info(f"Pureva route by intent: conv_id={state.get('conv_id')} intent={intent} -> {next_node}")
    return next_node
