"""Tests for idempotent SEC persistence operations."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import finsight.storage.repositories as repositories_module
from finsight.storage.models import Company, Filing, FilingSection
from finsight.storage.repositories import (
    CompanyUpsert,
    FilingChunkCreate,
    FilingCreate,
    FilingIdentityConflictError,
    FilingPersistenceError,
    FilingSectionCreate,
    FinancialFactCreate,
    find_existing_accession_numbers,
    get_filing_by_accession_number,
    store_filing,
    store_filing_content,
    store_financial_facts,
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
async def test_store_filing_content_builds_sections_and_chunks() -> None:
    """Parsed retrieval records should be attached and flushed in one transaction."""

    session = AsyncMock(spec=AsyncSession)
    filing_id = uuid4()
    section_command = FilingSectionCreate(
        section_name="ITEM 1A. RISK FACTORS",
        sequence_number=0,
        content="Cybersecurity risks could affect operations.",
        content_hash="b" * 64,
        char_count=43,
        source_metadata={"parser_version": "sec-html-v1"},
        chunks=(
            FilingChunkCreate(
                chunk_index=0,
                content="Cybersecurity risks could affect operations.",
                content_hash="c" * 64,
                token_count=8,
                source_metadata={"token_start": 0, "token_end": 8},
            ),
        ),
    )

    stored = await store_filing_content(
        session,
        filing_id,
        [section_command],
    )

    assert stored.section_count == 1
    assert stored.chunk_count == 1
    session.add_all.assert_called_once()
    session.flush.assert_awaited_once()

    section_models = session.add_all.call_args.args[0]
    assert len(section_models) == 1
    section = section_models[0]
    assert isinstance(section, FilingSection)
    assert section.filing_id == filing_id
    assert section.section_name == "ITEM 1A. RISK FACTORS"
    assert section.source_metadata == {"parser_version": "sec-html-v1"}
    assert len(section.chunks) == 1
    assert section.chunks[0].chunk_index == 0
    assert section.chunks[0].token_count == 8


def financial_fact_command() -> FinancialFactCreate:
    """Return one exact normalized financial observation."""

    return FinancialFactCreate(
        observation_key="d" * 64,
        taxonomy="us-gaap",
        concept="Revenues",
        label="Revenue",
        description="Revenue from customers.",
        unit="USD",
        value=Decimal("85777000000"),
        start_date=date(2024, 4, 1),
        end_date=date(2024, 6, 29),
        filed_date=date(2024, 8, 2),
        fiscal_year=2024,
        fiscal_period="Q3",
        form_type="10-Q",
        accession_number="0000320193-24-000123",
        frame="CY2024Q2",
        source_metadata={"provider": "sec-companyfacts"},
    )


@pytest.mark.asyncio
async def test_store_financial_facts_avoids_empty_database_query() -> None:
    """An empty normalized batch should return without executing SQL."""

    session = AsyncMock(spec=AsyncSession)

    stored = await store_financial_facts(session, uuid4(), [])

    assert stored.created_count == 0
    assert stored.existing_count == 0
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_financial_facts_reports_created_and_existing_rows() -> None:
    """Bulk fact inserts should report conflicts without duplicating observations."""

    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalars.return_value.all.return_value = [uuid4()]
    session.execute.return_value = result
    fact = financial_fact_command()

    stored = await store_financial_facts(session, uuid4(), [fact, fact])

    assert stored.created_count == 1
    assert stored.existing_count == 1
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_financial_facts_bounds_insert_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large XBRL histories should not exceed PostgreSQL parameter limits."""

    monkeypatch.setattr(repositories_module, "MAX_FINANCIAL_FACTS_PER_INSERT", 1)
    session = AsyncMock(spec=AsyncSession)
    first_result = Mock()
    first_result.scalars.return_value.all.return_value = [uuid4()]
    second_result = Mock()
    second_result.scalars.return_value.all.return_value = [uuid4()]
    session.execute.side_effect = [first_result, second_result]
    facts = [financial_fact_command(), financial_fact_command()]

    stored = await store_financial_facts(session, uuid4(), facts)

    assert stored.created_count == 2
    assert stored.existing_count == 0
    assert session.execute.await_count == 2


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
