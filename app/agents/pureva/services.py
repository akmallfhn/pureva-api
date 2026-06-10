"""Service layer Pureva: query knowledge base SQLite + kirim pesan WhatsApp.

Semua akses data context (treatment, jadwal dokter, diskon, pasien) lewat sini
supaya node graph tetap tipis dan mudah dites.
"""

import logging
from datetime import datetime

import httpx

from app.core.config import settings
from app.db.database import get_connection

logger = logging.getLogger(__name__)

DAY_NAMES_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def today_day_name() -> str:
    """Nama hari Bahasa Indonesia untuk hari ini (mengikuti diskon harian)."""
    return DAY_NAMES_ID[datetime.now().weekday()]


# --------------------------------------------------------------------------- #
# Treatments / produk
# --------------------------------------------------------------------------- #
def list_treatments(limit: int | None = None) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT name, description, category, price_text, price_amount FROM treatments ORDER BY category, price_amount"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def list_categories() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT category FROM treatments WHERE category != '' ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]
    finally:
        conn.close()


def search_treatments(keywords: list[str], limit: int = 15) -> list[dict]:
    """Cari treatment yang relevan dengan keyword (di nama / deskripsi / kategori)."""
    keywords = [k.strip().lower() for k in keywords if k and k.strip()]
    if not keywords:
        return list_treatments(limit=limit)

    conn = get_connection()
    try:
        clauses = []
        params: list[str] = []
        for kw in keywords:
            clauses.append("(LOWER(name) LIKE ? OR LOWER(description) LIKE ? OR LOWER(category) LIKE ?)")
            like = f"%{kw}%"
            params.extend([like, like, like])
        sql = (
            "SELECT name, description, category, price_text, price_amount FROM treatments "
            f"WHERE {' OR '.join(clauses)} ORDER BY price_amount LIMIT {int(limit)}"
        )
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Jadwal dokter
# --------------------------------------------------------------------------- #
def list_doctors() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name, day, hours, profile_url FROM doctors ORDER BY name, id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def doctors_on_day(day: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name, day, hours, profile_url FROM doctors WHERE LOWER(day) = LOWER(?) ORDER BY name",
            (day.strip(),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Diskon
# --------------------------------------------------------------------------- #
def list_discounts() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT day, promo_name, discount_percent, applies_to FROM discounts"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def discount_for_day(day: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT day, promo_name, discount_percent, applies_to FROM discounts WHERE LOWER(day) = LOWER(?)",
            (day.strip(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Booking
# --------------------------------------------------------------------------- #
def create_booking(
    *,
    conv_id: str,
    phone: str,
    name: str,
    treatment: str,
    doctor: str,
    day: str,
    hours: str,
    price_text: str,
    discount_percent: int,
    status: str = "TENTATIVE",
) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO bookings
               (conv_id, phone, name, treatment, doctor, day, hours, price_text, discount_percent, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (conv_id, phone, name, treatment, doctor, day, hours, price_text, discount_percent, status),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Memory / histori percakapan (Shared State)
# --------------------------------------------------------------------------- #
def save_message(conv_id: str, role: str, content: str, intent: str = "") -> None:
    if not conv_id or not content:
        return
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO conversations (conv_id, role, content, intent) VALUES (?, ?, ?, ?)",
            (conv_id, role, content, intent),
        )
        conn.commit()
    finally:
        conn.close()


def recent_history(conv_id: str, limit: int = 10) -> list[dict]:
    if not conv_id:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content, intent, created_at FROM conversations "
            "WHERE conv_id = ? ORDER BY id DESC LIMIT ?",
            (conv_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Kirim pesan WhatsApp (Meta Cloud API) - dry-run kalau token kosong
# --------------------------------------------------------------------------- #
async def send_whatsapp_message(phone: str, message: str) -> dict:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        logger.info(f"[DRY-RUN WA -> {phone}] {message}")
        return {"dry_run": True, "to": phone, "message": message}

    url = f"{settings.whatsapp_api_url}/{settings.whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message[:4096]},
    }
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()
