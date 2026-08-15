"""Tests for asynchronous database infrastructure."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from finsight.config.settings import DEFAULT_DATABASE_URL, Settings
from finsight.storage.database import (
    SessionFactory,
    check_database_connection,
    create_database_engine,
    create_session_factory,
    session_scope,
)


def test_create_database_engine_uses_safe_pool_configuration() -> None:
    """Engine construction should honor the validated runtime settings."""

    settings = Settings(
        database_pool_size=7,
        database_max_overflow=14,
        database_pool_timeout_seconds=45,
    )
    engine = MagicMock(spec=AsyncEngine)

    with patch(
        "finsight.storage.database.create_async_engine",
        return_value=engine,
    ) as mocked_create_engine:
        result = create_database_engine(settings)

    assert result is engine
    mocked_create_engine.assert_called_once_with(
        DEFAULT_DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=7,
        max_overflow=14,
        pool_timeout=45,
    )


def test_create_session_factory_configures_explicit_transactions() -> None:
    """Sessions should not autoflush or expire objects after commit."""

    engine = MagicMock(spec=AsyncEngine)
    session_factory = MagicMock()

    with patch(
        "finsight.storage.database.async_sessionmaker",
        return_value=session_factory,
    ) as mocked_sessionmaker:
        result = create_session_factory(engine)

    assert result is session_factory
    mocked_sessionmaker.assert_called_once_with(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


@pytest.mark.asyncio
async def test_session_scope_commits_successful_work() -> None:
    """Successful work should be committed exactly once."""

    session = AsyncMock(spec=AsyncSession)
    session_context = MagicMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    session_factory_mock = MagicMock(return_value=session_context)
    session_factory = cast(SessionFactory, session_factory_mock)

    async with session_scope(session_factory) as yielded_session:
        assert yielded_session is session

    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    session_context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_scope_rolls_back_failed_work() -> None:
    """Failed work should be rolled back and the original error re-raised."""

    session = AsyncMock(spec=AsyncSession)
    session_context = MagicMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    session_factory_mock = MagicMock(return_value=session_context)
    session_factory = cast(SessionFactory, session_factory_mock)

    with pytest.raises(RuntimeError, match="forced failure"):
        async with session_scope(session_factory):
            raise RuntimeError("forced failure")

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
    session_context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_database_connection_executes_probe_query() -> None:
    """The connectivity probe should execute a lightweight SQL statement."""

    engine = MagicMock(spec=AsyncEngine)
    connection = AsyncMock(spec=AsyncConnection)
    connection_context = MagicMock()
    connection_context.__aenter__.return_value = connection
    connection_context.__aexit__.return_value = None
    engine.connect.return_value = connection_context

    await check_database_connection(engine)

    connection.execute.assert_awaited_once()
    execute_call = connection.execute.await_args
    assert execute_call is not None
    assert str(execute_call.args[0]) == "SELECT 1"
    connection_context.__aexit__.assert_awaited_once()
