"""Integration tests for the PostgreSQL and pgvector runtime."""

import os
from datetime import date

import pytest
from sqlalchemy import delete, text

from finsight.config.settings import Settings
from finsight.storage.database import (
    check_database_connection,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from finsight.storage.models import Company
from finsight.storage.repositories import (
    CompanyUpsert,
    FilingCreate,
    store_filing,
    upsert_company,
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


@pytest.mark.asyncio
async def test_migrations_create_sec_filing_tables() -> None:
    """Alembic should create the SEC filing persistence schema."""

    engine = create_database_engine(Settings())

    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' "
                    "AND tablename IN "
                    "('companies', 'filings', 'filing_sections', 'filing_chunks')"
                )
            )
            tables = set(result.scalars().all())
    finally:
        await engine.dispose()

    assert tables == {
        "companies",
        "filings",
        "filing_sections",
        "filing_chunks",
    }


@pytest.mark.asyncio
async def test_company_and_filing_persistence_is_idempotent() -> None:
    """Repeated SEC discovery should not create duplicate database records."""

    test_cik = "0000000001"
    engine = create_database_engine(Settings())
    session_factory = create_session_factory(engine)

    try:
        async with session_scope(session_factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == test_cik))

        async with session_scope(session_factory) as session:
            company_command = CompanyUpsert(
                cik=test_cik,
                legal_name="FinSight Integration Test Company",
                ticker="TEST",
                sic="7372",
                fiscal_year_end="1231",
            )
            first_company = await upsert_company(session, company_command)
            second_company = await upsert_company(session, company_command)

            filing_command = FilingCreate(
                company_id=first_company.id,
                accession_number="0000000001-24-000001",
                form_type="10-K",
                filing_date=date(2024, 12, 31),
                report_date=date(2024, 12, 31),
                primary_document="integration-test.htm",
                source_url=(
                    "https://www.sec.gov/Archives/edgar/data/"
                    "1/000000000124000001/integration-test.htm"
                ),
                content_hash="c" * 64,
                source_metadata={"test": True},
            )
            first_filing = await store_filing(session, filing_command)
            second_filing = await store_filing(session, filing_command)

            assert first_company.id == second_company.id
            assert first_filing.created is True
            assert second_filing.created is False
            assert first_filing.filing.id == second_filing.filing.id
    finally:
        async with session_scope(session_factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == test_cik))
        await engine.dispose()
