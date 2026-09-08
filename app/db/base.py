from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base entity Postgres. DDL referensinya di docs/db, bukan digenerate SQLAlchemy."""

    # Semua kolom waktu di Postgres timestamptz; tanpa ini asyncpg menolak datetime yang aware.
    type_annotation_map = {datetime: DateTime(timezone=True)}
