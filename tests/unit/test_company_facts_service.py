"""Tests for SEC company-facts ingestion orchestration."""

from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import finsight.ingestion.company_facts_service as service_module
from finsight.ingestion.company_facts import SecCompanyFacts
from finsight.ingestion.company_facts_service import ingest_company_facts
from finsight.ingestion.sec_client import SecEdgarClient
from finsight.ingestion.sec_schemas import SecCompanySubmissions
from finsight.storage.database import SessionFactory
from finsight.storage.models import Company
from finsight.storage.repositories import StoredFinancialFacts

COMPANY_ID = UUID("33333333-3333-4333-8333-333333333333")


def sample_submissions() -> SecCompanySubmissions:
    """Return issuer metadata used to refresh the company record."""

    return SecCompanySubmissions.model_validate(
        {
            "cik": 320193,
            "name": "Apple Inc.",
            "tickers": ["AAPL"],
            "exchanges": ["Nasdaq"],
            "sic": "3571",
            "fiscalYearEnd": "0927",
            "filings": {"recent": {}, "files": []},
        }
    )


def sample_company_facts() -> SecCompanyFacts:
    """Return facts across a selected and an excluded taxonomy."""

    payload: dict[str, Any] = {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "dei": {
                "EntityPublicFloat": {
                    "label": "Public Float",
                    "description": "Issuer public float.",
                    "units": {
                        "USD": [
                            {
                                "end": "2024-06-29",
                                "val": 100,
                                "accn": "0000320193-24-000123",
                                "form": "10-Q",
                                "filed": "2024-08-02",
                            }
                        ]
                    },
                }
            },
            "us-gaap": {
                "Assets": {
                    "label": "Assets",
                    "description": "Total assets.",
                    "units": {
                        "USD": [
                            {
                                "end": "2024-06-29",
                                "val": 331612000000,
                                "accn": "0000320193-24-000123",
                                "fy": 2024,
                                "fp": "Q3",
                                "form": "10-Q",
                                "filed": "2024-08-02",
                            }
                        ]
                    },
                }
            },
        },
    }
    return SecCompanyFacts.model_validate(payload)


def company_model() -> Company:
    """Return the company produced by the mocked repository."""

    return Company(
        id=COMPANY_ID,
        cik="0000320193",
        legal_name="Apple Inc.",
        ticker="AAPL",
        sic="3571",
        fiscal_year_end="0927",
    )


def fake_session_factory(session: AsyncMock) -> SessionFactory:
    """Return one asynchronous mocked session factory."""

    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    return cast(SessionFactory, Mock(return_value=session))


@pytest.mark.asyncio
async def test_company_facts_service_filters_and_persists_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selected taxonomy records should be normalized and stored once."""

    client = AsyncMock(spec=SecEdgarClient)
    client.fetch_company_submissions.return_value = sample_submissions()
    client.fetch_company_facts.return_value = sample_company_facts()
    session = AsyncMock(spec=AsyncSession)

    upsert_mock = AsyncMock(return_value=company_model())
    store_mock = AsyncMock(return_value=StoredFinancialFacts(created_count=1, existing_count=0))
    monkeypatch.setattr(service_module, "upsert_company", upsert_mock)
    monkeypatch.setattr(service_module, "store_financial_facts", store_mock)

    result = await ingest_company_facts(
        client=client,
        session_factory=fake_session_factory(session),
        cik="320193",
        taxonomies={" US-GAAP "},
    )

    assert result.cik == "0000320193"
    assert result.company_id == COMPANY_ID
    assert result.discovered_observations == 2
    assert result.selected_observations == 1
    assert result.created_observations == 1
    assert result.skipped_existing_observations == 0
    assert result.selected_taxonomies == ("us-gaap",)
    client.fetch_company_facts.assert_awaited_once_with("0000320193")
    upsert_mock.assert_awaited_once()
    store_mock.assert_awaited_once()
    session.commit.assert_awaited_once()

    assert store_mock.await_args is not None
    commands = store_mock.await_args.args[2]
    assert len(commands) == 1
    assert commands[0].concept == "Assets"
    assert commands[0].source_metadata == {
        "provider": "sec-companyfacts",
        "entity_name": "Apple Inc.",
    }


@pytest.mark.asyncio
async def test_company_facts_service_reports_existing_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repository conflicts should remain visible in the ingestion result."""

    client = AsyncMock(spec=SecEdgarClient)
    client.fetch_company_submissions.return_value = sample_submissions()
    client.fetch_company_facts.return_value = sample_company_facts()
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(
        service_module,
        "upsert_company",
        AsyncMock(return_value=company_model()),
    )
    monkeypatch.setattr(
        service_module,
        "store_financial_facts",
        AsyncMock(return_value=StoredFinancialFacts(created_count=0, existing_count=2)),
    )

    result = await ingest_company_facts(
        client=client,
        session_factory=fake_session_factory(session),
        cik="320193",
    )

    assert result.selected_observations == 2
    assert result.created_observations == 0
    assert result.skipped_existing_observations == 2
    assert result.selected_taxonomies == ("dei", "us-gaap")


@pytest.mark.asyncio
async def test_company_facts_service_rejects_empty_taxonomy_selection() -> None:
    """At least one nonblank taxonomy is required before making SEC requests."""

    client = AsyncMock(spec=SecEdgarClient)

    with pytest.raises(ValueError, match="at least one"):
        await ingest_company_facts(
            client=client,
            session_factory=cast(SessionFactory, Mock()),
            cik="320193",
            taxonomies={"", "   "},
        )

    client.fetch_company_submissions.assert_not_awaited()
