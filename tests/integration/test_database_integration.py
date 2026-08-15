"""Integration tests for the PostgreSQL and pgvector runtime."""

import os

import pytest
from sqlalchemy import text

from finsight.config.settings import Settings
from finsight.storage.database import (
    check_database_connection,
    create_database_engine,
)

RUN_DATABASE_TESTS = os.getenv("FINSIGHT_RUN_DATABASE_TESTS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason="set FINSIGHT_RUN_DATABASE_TESTS=1 to run database integration tests",
    ),
]


@pytest.mark.asyncio
async def test_postgres_connection_and_required_extensions() -> None:
    """The configured database should expose pgvector and trigram search."""

    engine = create_database_engine(Settings())

    try:
        await check_database_connection(engine)

        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT extname, extversion "
                    "FROM pg_extension "
                    "WHERE extname IN ('vector', 'pg_trgm')"
                )
            )
            extensions = dict(result.tuples().all())
    finally:
        await engine.dispose()

    assert extensions["vector"] == "0.8.6"
    assert "pg_trgm" in extensions
