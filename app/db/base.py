from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base entity Postgres. Schema dimiliki Prisma di pureva-ai — repo ini tidak pernah DDL."""

    # Semua kolom waktu di sana timestamptz; tanpa ini asyncpg menolak datetime yang aware.
    type_annotation_map = {datetime: DateTime(timezone=True)}
