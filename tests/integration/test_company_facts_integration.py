"""End-to-end integration tests for SEC company-facts persistence."""

import os
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select

from finsight.config.settings import Settings
from finsight.ingestion.company_facts_service import ingest_company_facts
from finsight.ingestion.sec_client import SecEdgarClient
from finsight.storage.database import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from finsight.storage.models import Company, FinancialFact

RUN_DATABASE_TESTS = os.getenv("FINSIGHT_RUN_DATABASE_TESTS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason="set FINSIGHT_RUN_DATABASE_TESTS=1 to run database integration tests",
    ),
]


@pytest.mark.asyncio
async def test_company_facts_ingestion_is_idempotent_in_postgres() -> None:
    """Repeated XBRL ingestion should preserve exact values without duplicates."""

    test_cik = "0000000003"
    accession_number = "0000000003-24-000001"
    submissions_url = "https://data.sec.gov/submissions/CIK0000000003.json"
    facts_url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000003.json"
    submissions_payload: dict[str, Any] = {
        "cik": 3,
        "name": "FinSight Facts Integration Company",
        "tickers": ["FCT"],
        "exchanges": ["Test Exchange"],
        "sic": "7372",
        "fiscalYearEnd": "1231",
        "filings": {"recent": {}, "files": []},
    }
    facts_payload: dict[str, Any] = {
        "cik": 3,
        "entityName": "FinSight Facts Integration Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "description": "Revenue from customers.",
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": "123456789.125",
                                "accn": accession_number,
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-02-01",
                                "frame": "CY2024",
                            }
                        ]
                    },
                }
            }
        },
    }
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))

        if str(request.url) == submissions_url:
            return httpx.Response(200, json=submissions_payload)
        if str(request.url) == facts_url:
            return httpx.Response(200, json=facts_payload)

        return httpx.Response(404)

    settings = Settings(sec_user_agent="FinSightAI/0.1 integration@example.org")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    transport = httpx.MockTransport(handler)

    try:
        async with session_scope(session_factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == test_cik))

        async with (
            httpx.AsyncClient(transport=transport) as http_client,
            SecEdgarClient(settings, http_client=http_client) as sec_client,
        ):
            first_result = await ingest_company_facts(
                client=sec_client,
                session_factory=session_factory,
                cik=test_cik,
                taxonomies={"us-gaap"},
            )
            second_result = await ingest_company_facts(
                client=sec_client,
                session_factory=session_factory,
                cik=test_cik,
                taxonomies={"us-gaap"},
            )

        async with session_factory() as verification_session:
            result = await verification_session.execute(
                select(FinancialFact).join(Company).where(Company.cik == test_cik)
            )
            fact = result.scalar_one()

        assert first_result.created_observations == 1
        assert first_result.skipped_existing_observations == 0
        assert second_result.created_observations == 0
        assert second_result.skipped_existing_observations == 1
        assert requested_urls.count(submissions_url) == 2
        assert requested_urls.count(facts_url) == 2
        assert fact.value == Decimal("123456789.1250000000")
        assert fact.taxonomy == "us-gaap"
        assert fact.concept == "Revenues"
        assert fact.accession_number == accession_number
        assert fact.source_metadata == {
            "provider": "sec-companyfacts",
            "entity_name": "FinSight Facts Integration Company",
        }
    finally:
        async with session_scope(session_factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == test_cik))
        await engine.dispose()
