"""Koneksi async SQLAlchemy ke Postgres pureva; engine lazy agar import jalan tanpa DATABASE_URL."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# Supabase transaction pooler; PgBouncer mode ini tidak mendukung prepared statement.
POOLER_PORT = 6543

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def normalize_database_url(url: str) -> tuple[str, dict]:
    """Ubah URL Prisma/libpq ke asyncpg; param libpq yang ditolak dipindah ke connect_args."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    connect_args: dict = {}

    sslmode = (query.get("sslmode") or [""])[0]
    if sslmode and sslmode != "disable":
        connect_args["ssl"] = "require" if sslmode in ("require", "prefer", "allow") else sslmode

    pgbouncer = (query.get("pgbouncer") or [""])[0] == "true"
    if pgbouncer or parts.port == POOLER_PORT:
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0

    return urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, "", "")), connect_args


def init_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL must be set: the WhatsApp webhook persists straight into"
            " the pureva Postgres"
        )

    url, connect_args = normalize_database_url(settings.database_url)
    _engine = create_async_engine(
        url,
        connect_args=connect_args,
        # Sisakan headroom di bawah pool size Supabase (default 15 di Nano).
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=1800,
        pool_pre_ping=True,
        echo=settings.debug,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    logger.info(f"database engine ready: {urlsplit(url).hostname}")
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session untuk background task, yang tidak ikut siklus hidup request."""
    init_engine()
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependency FastAPI untuk endpoint yang query-nya jalan di dalam request."""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
