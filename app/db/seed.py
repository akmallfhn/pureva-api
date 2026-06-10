"""Seed SQLite dari file CSV di app/db/data.

Jalankan: `uv run seed`  atau  `python -m app.db.seed`
Idempotent: pakai INSERT OR IGNORE / REPLACE jadi aman dijalankan berulang.
"""

import csv
import logging
from pathlib import Path

from app.db.database import (
    get_connection,
    init_schema,
    parse_price,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"

# Sumber CSV untuk katalog treatment. Kalau user menaruh file CSV asli yang
# lebih lengkap dengan nama yang sama, seed akan otomatis ikut memuatnya.
TREATMENT_FILES = ["product_aesthetic.csv", "product_skin_health.csv"]


def _read_csv(filename: str) -> list[dict]:
    path = DATA_DIR / filename
    if not path.exists():
        logger.warning(f"seed: file CSV tidak ditemukan, dilewati: {path}")
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def seed_treatments(conn) -> int:
    for filename in TREATMENT_FILES:
        for row in _read_csv(filename):
            name = (row.get("product") or "").strip()
            if not name:
                continue
            price_text = (row.get("price_start_from") or "").strip()
            conn.execute(
                """INSERT OR IGNORE INTO treatments
                   (name, description, category, price_text, price_amount)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    name,
                    (row.get("description") or "").strip(),
                    (row.get("category") or "").strip(),
                    price_text,
                    parse_price(price_text),
                ),
            )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM treatments").fetchone()[0]


def seed_doctors(conn) -> int:
    for row in _read_csv("practice_schedule.csv"):
        name = (row.get("name") or "").strip()
        day = (row.get("day") or "").strip()
        if not name or not day:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO doctors (name, day, hours, profile_url)
               VALUES (?, ?, ?, ?)""",
            (
                name,
                day,
                (row.get("hours") or "").strip(),
                (row.get("profile_url") or "").strip(),
            ),
        )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]


def seed_discounts(conn) -> int:
    for row in _read_csv("practice_discount.csv"):
        day = (row.get("day") or "").strip()
        if not day:
            continue
        try:
            percent = int(float(row.get("discount_percent") or 0))
        except ValueError:
            percent = 0
        conn.execute(
            """INSERT OR REPLACE INTO discounts (day, promo_name, discount_percent, applies_to)
               VALUES (?, ?, ?, ?)""",
            (
                day,
                (row.get("promo_name") or "").strip(),
                percent,
                (row.get("applies_to") or "").strip(),
            ),
        )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM discounts").fetchone()[0]


def seed_all() -> dict[str, int]:
    conn = get_connection()
    try:
        init_schema(conn)
        result = {
            "treatments": seed_treatments(conn),
            "doctors": seed_doctors(conn),
            "discounts": seed_discounts(conn),
        }
        return result
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    result = seed_all()
    print("Seed selesai. Isi database:")
    for table, n in result.items():
        print(f"  - {table}: {n} baris")


if __name__ == "__main__":
    main()
