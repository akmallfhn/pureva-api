"""SQLite connection + schema untuk knowledge base Pureva.

Semua context (treatment, jadwal dokter, diskon, data pasien) di-seed dari CSV
ke SQLite supaya node-node graph bisa query secara cepat dan deterministik.
"""

import re
import sqlite3
from pathlib import Path

from app.core.config import settings

DB_PATH = Path(settings.database_path)

SCHEMA = """
CREATE TABLE IF NOT EXISTS treatments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    description   TEXT,
    category      TEXT,
    price_text    TEXT,
    price_amount  INTEGER,
    UNIQUE(name, category, price_text)
);

CREATE TABLE IF NOT EXISTS doctors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    day          TEXT NOT NULL,
    hours        TEXT,
    profile_url  TEXT,
    UNIQUE(name, day)
);

CREATE TABLE IF NOT EXISTS discounts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    day               TEXT NOT NULL UNIQUE,
    promo_name        TEXT,
    discount_percent  INTEGER,
    applies_to        TEXT
);

CREATE TABLE IF NOT EXISTS bookings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id           TEXT,
    phone             TEXT,
    name              TEXT,
    treatment         TEXT,
    doctor            TEXT,
    day               TEXT,
    hours             TEXT,
    price_text        TEXT,
    discount_percent  INTEGER,
    status            TEXT DEFAULT 'TENTATIVE',
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id     TEXT,
    role        TEXT,
    content     TEXT,
    intent      TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def parse_price(price_text: str | None) -> int:
    """'Rp1,360,000' -> 1360000. Kembalikan 0 kalau tidak bisa di-parse."""
    if not price_text:
        return 0
    digits = re.sub(r"[^0-9]", "", price_text)
    return int(digits) if digits else 0


def database_exists() -> bool:
    return DB_PATH.exists() and DB_PATH.stat().st_size > 0
