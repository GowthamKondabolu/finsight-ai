"""Asynchronous PostgreSQL engine and transaction management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from finsight.config.settings import Settings

SessionFactory = async_sessionmaker[AsyncSession]


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create a pooled asynchronous PostgreSQL engine."""

    return create_async_engine(
        settings.database_url.get_secret_value(),
        echo=settings.database_echo,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
    )


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create sessions with explicit transaction and refresh behavior."""

    return async_sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


@asynccontextmanager
async def session_scope(
    session_factory: SessionFactory,
) -> AsyncIterator[AsyncSession]:
    """Commit successful work and roll back failed transactions."""

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database_connection(engine: AsyncEngine) -> None:
    """Execute a lightweight query to verify database connectivity."""

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
