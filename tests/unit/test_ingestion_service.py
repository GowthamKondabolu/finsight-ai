"""Tests for transaction-aware SEC ingestion orchestration."""

from datetime import date
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import finsight.ingestion.service as service_module
from finsight.ingestion.sec_client import SecEdgarClient, SecFilingDocument
from finsight.ingestion.sec_schemas import SecCompanySubmissions
from finsight.ingestion.service import ingest_company_filings
from finsight.storage.database import SessionFactory
from finsight.storage.models import Company, Filing
from finsight.storage.repositories import StoredFiling

COMPANY_ID = UUID("11111111-1111-4111-8111-111111111111")


def sample_submissions() -> SecCompanySubmissions:
    """Return filings containing selected, existing, and excluded forms."""

    payload: dict[str, Any] = {
        "cik": "320193",
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "sic": "3571",
        "fiscalYearEnd": "0927",
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0000320193-24-000123",
                    "0000320193-24-000124",
                    "0000320193-24-000125",
                ],
                "filingDate": [
                    "2024-08-02",
                    "2024-08-03",
                    "2024-08-04",
                ],
                "reportDate": ["2024-06-29", "", ""],
                "form": ["10-K", "8-K", "4"],
                "primaryDocument": [
                    "annual.htm",
                    "current.htm",
                    "ownership.htm",
                ],
            },
            "files": [],
        },
    }
    return SecCompanySubmissions.model_validate(payload)


def company_model() -> Company:
    """Return the company produced by the mocked upsert."""

    return Company(
        id=COMPANY_ID,
        cik="0000320193",
        legal_name="Apple Inc.",
        ticker="AAPL",
        sic="3571",
        fiscal_year_end="0927",
    )


def stored_filing(*, created: bool) -> StoredFiling:
    """Return a representative repository result."""

    filing = Filing(
        company_id=COMPANY_ID,
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=date(2024, 8, 2),
        report_date=date(2024, 6, 29),
        primary_document="annual.htm",
        source_url="https://www.sec.gov/annual.htm",
        content_hash="a" * 64,
        source_metadata={},
    )
    return StoredFiling(filing=filing, created=created)


def fake_session_factory(
    *sessions: AsyncMock,
) -> tuple[SessionFactory, Mock]:
    """Return a callable session factory and its assertion handle."""

    for session in sessions:
        session.__aenter__.return_value = session
        session.__aexit__.return_value = None

    factory = Mock(side_effect=sessions)
    return cast(SessionFactory, factory), factory


@pytest.mark.asyncio
async def test_ingestion_filters_skips_downloads_and_persists_new_filings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only selected, non-existing filings should be downloaded and stored."""

    submissions = sample_submissions()
    document = SecFilingDocument(
        source_url=("https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/annual.htm"),
        content=b"<html>annual filing</html>",
        content_hash="a" * 64,
        content_type="text/html",
    )
    client = AsyncMock(spec=SecEdgarClient)
    client.fetch_company_submissions.return_value = submissions
    client.fetch_filing_document.return_value = document

    read_session = AsyncMock(spec=AsyncSession)
    write_session = AsyncMock(spec=AsyncSession)
    session_factory, factory = fake_session_factory(
        read_session,
        write_session,
    )

    existing_mock = AsyncMock(return_value={"0000320193-24-000124"})
    upsert_mock = AsyncMock(return_value=company_model())
    store_mock = AsyncMock(return_value=stored_filing(created=True))
    monkeypatch.setattr(
        service_module,
        "find_existing_accession_numbers",
        existing_mock,
    )
    monkeypatch.setattr(service_module, "upsert_company", upsert_mock)
    monkeypatch.setattr(service_module, "store_filing", store_mock)

    result = await ingest_company_filings(
        client=client,
        session_factory=session_factory,
        cik="320193",
        forms={"10-K", "8-K"},
        limit=2,
    )

    assert result.cik == "0000320193"
    assert result.company_id == COMPANY_ID
    assert result.discovered_filings == 3
    assert result.selected_filings == 2
    assert result.downloaded_filings == 1
    assert result.created_filings == 1
    assert result.skipped_existing_filings == 1
    assert result.selected_forms == ("10-K", "8-K")
    assert factory.call_count == 2

    client.fetch_filing_document.assert_awaited_once()
    existing_mock.assert_awaited_once()
    upsert_mock.assert_awaited_once()
    store_mock.assert_awaited_once()

    assert store_mock.await_args is not None
    filing_command = store_mock.await_args.args[1]
    assert filing_command.company_id == COMPANY_ID
    assert filing_command.content_hash == "a" * 64
    assert filing_command.source_metadata == {
        "provider": "sec-edgar",
        "content_type": "text/html",
        "content_length": 26,
    }
    write_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingestion_handles_no_matching_filings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A company should still be refreshed when no filing matches."""

    client = AsyncMock(spec=SecEdgarClient)
    client.fetch_company_submissions.return_value = sample_submissions()

    write_session = AsyncMock(spec=AsyncSession)
    session_factory, factory = fake_session_factory(write_session)

    existing_mock = AsyncMock()
    upsert_mock = AsyncMock(return_value=company_model())
    store_mock = AsyncMock()
    monkeypatch.setattr(
        service_module,
        "find_existing_accession_numbers",
        existing_mock,
    )
    monkeypatch.setattr(service_module, "upsert_company", upsert_mock)
    monkeypatch.setattr(service_module, "store_filing", store_mock)

    result = await ingest_company_filings(
        client=client,
        session_factory=session_factory,
        cik="320193",
        forms={"S-1"},
    )

    assert result.selected_filings == 0
    assert result.downloaded_filings == 0
    assert result.created_filings == 0
    assert result.skipped_existing_filings == 0
    assert factory.call_count == 1
    existing_mock.assert_not_awaited()
    client.fetch_filing_document.assert_not_awaited()
    store_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingestion_counts_concurrent_duplicate_as_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filing inserted by another worker should remain idempotent."""

    client = AsyncMock(spec=SecEdgarClient)
    client.fetch_company_submissions.return_value = sample_submissions()
    client.fetch_filing_document.return_value = SecFilingDocument(
        source_url="https://www.sec.gov/annual.htm",
        content=b"annual",
        content_hash="a" * 64,
        content_type=None,
    )

    read_session = AsyncMock(spec=AsyncSession)
    write_session = AsyncMock(spec=AsyncSession)
    session_factory, _ = fake_session_factory(
        read_session,
        write_session,
    )

    monkeypatch.setattr(
        service_module,
        "find_existing_accession_numbers",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        service_module,
        "upsert_company",
        AsyncMock(return_value=company_model()),
    )
    monkeypatch.setattr(
        service_module,
        "store_filing",
        AsyncMock(return_value=stored_filing(created=False)),
    )

    result = await ingest_company_filings(
        client=client,
        session_factory=session_factory,
        cik="320193",
        forms={"10-K"},
        limit=1,
    )

    assert result.selected_filings == 1
    assert result.downloaded_filings == 1
    assert result.created_filings == 0
    assert result.skipped_existing_filings == 1


@pytest.mark.asyncio
async def test_ingestion_rejects_empty_form_selection() -> None:
    """At least one nonblank filing form is required."""

    client = AsyncMock(spec=SecEdgarClient)
    session_factory = cast(SessionFactory, Mock())

    with pytest.raises(ValueError, match="at least one"):
        await ingest_company_filings(
            client=client,
            session_factory=session_factory,
            cik="320193",
            forms={"", "   "},
        )

    client.fetch_company_submissions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 101])
async def test_ingestion_rejects_out_of_range_limits(limit: int) -> None:
    """Ingestion batch size must remain within its bounded policy."""

    client = AsyncMock(spec=SecEdgarClient)
    session_factory = cast(SessionFactory, Mock())

    with pytest.raises(ValueError, match="limit must be between"):
        await ingest_company_filings(
            client=client,
            session_factory=session_factory,
            cik="320193",
            limit=limit,
        )

    client.fetch_company_submissions.assert_not_awaited()
