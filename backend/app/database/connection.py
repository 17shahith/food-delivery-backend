"""Async SQLAlchemy engine, session factory and declarative base."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def build_engine_kwargs(url: str) -> dict[str, Any]:
    """Engine options. Postgres settings are compatible with Supabase's pooler (PgBouncer/Supavisor)."""
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("postgresql+asyncpg"):
        settings = get_settings()
        kwargs.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            connect_args={
                # Transaction-mode poolers do not support server-side prepared statement caching.
                "statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
            },
        )
    return kwargs


_settings = get_settings()
engine = create_async_engine(_settings.async_database_url, **build_engine_kwargs(_settings.async_database_url))
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session; uncommitted work is rolled back on exit."""
    async with SessionLocal() as session:
        yield session
