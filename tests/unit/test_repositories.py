"""Tests for idempotent SEC persistence operations."""

from datetime import date
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from finsight.storage.models import Company, Filing
from finsight.storage.repositories import (
    CompanyUpsert,
    FilingCreate,
    FilingIdentityConflictError,
    FilingPersistenceError,
    find_existing_accession_numbers,
    get_filing_by_accession_number,
    store_filing,
    upsert_company,
)


def company_command() -> CompanyUpsert:
    """Return a representative company upsert command."""

    return CompanyUpsert(
        cik="0000320193",
        legal_name="Apple Inc.",
        ticker="AAPL",
        sic="3571",
        fiscal_year_end="0927",
    )


def filing_command(
    *,
    company_id: UUID | None = None,
) -> FilingCreate:
    """Return a representative immutable filing command."""

    return FilingCreate(
        company_id=company_id or uuid4(),
        accession_number="0000320193-24-000123",
        form_type="10-Q",
        filing_date=date(2024, 8, 2),
        report_date=date(2024, 6, 29),
        primary_document="aapl-20240629.htm",
        source_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240629.htm"
        ),
        content_hash="a" * 64,
        source_metadata={"content_type": "text/html"},
    )


def filing_model(command: FilingCreate) -> Filing:
    """Create an ORM filing with the same immutable identity."""

    return Filing(
        company_id=command.company_id,
        accession_number=command.accession_number,
        form_type=command.form_type,
        filing_date=command.filing_date,
        report_date=command.report_date,
        primary_document=command.primary_document,
        source_url=command.source_url,
        content_hash=command.content_hash,
        source_metadata=command.source_metadata,
    )


@pytest.mark.asyncio
async def test_upsert_company_returns_database_record() -> None:
    """Company upserts should execute once and return the persisted entity."""

    session = AsyncMock(spec=AsyncSession)
    expected = Company(
        cik="0000320193",
        legal_name="Apple Inc.",
        ticker="AAPL",
        sic="3571",
        fiscal_year_end="0927",
    )
    result = Mock()
    result.scalar_one.return_value = expected
    session.execute.return_value = result

    stored = await upsert_company(session, company_command())

    assert stored is expected
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_existing_accessions_avoids_empty_database_query() -> None:
    """An empty discovery batch should not execute SQL."""

    session = AsyncMock(spec=AsyncSession)

    accessions = await find_existing_accession_numbers(session, [])

    assert accessions == set()
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_existing_accessions_returns_unique_values() -> None:
    """Persisted accession numbers should be returned as a set."""

    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalars.return_value.all.return_value = [
        "0000320193-24-000123",
        "0000320193-24-000123",
    ]
    session.execute.return_value = result

    accessions = await find_existing_accession_numbers(
        session,
        ["0000320193-24-000123"],
    )

    assert accessions == {"0000320193-24-000123"}
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_filing_by_accession_number_returns_record() -> None:
    """Repository lookup should return the matching filing."""

    session = AsyncMock(spec=AsyncSession)
    command = filing_command()
    expected = filing_model(command)
    result = Mock()
    result.scalar_one_or_none.return_value = expected
    session.execute.return_value = result

    filing = await get_filing_by_accession_number(
        session,
        command.accession_number,
    )

    assert filing is expected


@pytest.mark.asyncio
async def test_store_filing_returns_newly_inserted_record() -> None:
    """A new accession should be inserted exactly once."""

    session = AsyncMock(spec=AsyncSession)
    command = filing_command()
    expected = filing_model(command)
    result = Mock()
    result.scalar_one_or_none.return_value = expected
    session.execute.return_value = result

    stored = await store_filing(session, command)

    assert stored.filing is expected
    assert stored.created is True
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_filing_accepts_identical_existing_record() -> None:
    """Reprocessing identical SEC provenance should be idempotent."""

    session = AsyncMock(spec=AsyncSession)
    command = filing_command()
    existing = filing_model(command)

    insert_result = Mock()
    insert_result.scalar_one_or_none.return_value = None
    lookup_result = Mock()
    lookup_result.scalar_one_or_none.return_value = existing
    session.execute.side_effect = [insert_result, lookup_result]

    stored = await store_filing(session, command)

    assert stored.filing is existing
    assert stored.created is False
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_store_filing_rejects_missing_record_after_conflict() -> None:
    """An unresolved database conflict should fail explicitly."""

    session = AsyncMock(spec=AsyncSession)
    command = filing_command()

    insert_result = Mock()
    insert_result.scalar_one_or_none.return_value = None
    lookup_result = Mock()
    lookup_result.scalar_one_or_none.return_value = None
    session.execute.side_effect = [insert_result, lookup_result]

    with pytest.raises(
        FilingPersistenceError,
        match="no existing record was found",
    ):
        await store_filing(session, command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "conflicting_value"),
    [
        ("company_id", uuid4()),
        ("form_type", "8-K"),
        ("filing_date", date(2024, 8, 3)),
        ("report_date", None),
        ("primary_document", "different.htm"),
        ("source_url", "https://www.sec.gov/different.htm"),
        ("content_hash", "b" * 64),
    ],
)
async def test_store_filing_rejects_conflicting_immutable_identity(
    field_name: str,
    conflicting_value: object,
) -> None:
    """A reused accession number must preserve immutable SEC provenance."""

    session = AsyncMock(spec=AsyncSession)
    command = filing_command()
    existing = filing_model(command)
    setattr(existing, field_name, conflicting_value)

    insert_result = Mock()
    insert_result.scalar_one_or_none.return_value = None
    lookup_result = Mock()
    lookup_result.scalar_one_or_none.return_value = existing
    session.execute.side_effect = [insert_result, lookup_result]

    with pytest.raises(FilingIdentityConflictError, match=field_name):
        await store_filing(session, command)
