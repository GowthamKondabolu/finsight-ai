"""End-to-end integration tests for SEC ingestion and PostgreSQL storage."""

import hashlib
import os
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select

from finsight.config.settings import Settings
from finsight.ingestion.sec_client import SecEdgarClient
from finsight.ingestion.service import ingest_company_filings
from finsight.storage.database import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from finsight.storage.models import Company, Filing, FilingChunk, FilingSection

RUN_DATABASE_TESTS = os.getenv("FINSIGHT_RUN_DATABASE_TESTS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason="set FINSIGHT_RUN_DATABASE_TESTS=1 to run database integration tests",
    ),
]


@pytest.mark.asyncio
async def test_sec_ingestion_is_idempotent_across_http_and_postgres() -> None:
    """A repeated ingestion should reuse storage and skip document download."""

    test_cik = "0000000002"
    accession_number = "0000000002-24-000001"
    submissions_url = "https://data.sec.gov/submissions/CIK0000000002.json"
    filing_url = (
        "https://www.sec.gov/Archives/edgar/data/2/000000000224000001/integration-filing.htm"
    )
    filing_content = b"<html><body>Integration filing</body></html>"

    submissions_payload: dict[str, Any] = {
        "cik": test_cik,
        "name": "FinSight SEC Integration Company",
        "tickers": ["FST"],
        "exchanges": ["Test Exchange"],
        "sic": "7372",
        "fiscalYearEnd": "1231",
        "filings": {
            "recent": {
                "accessionNumber": [accession_number],
                "filingDate": ["2024-12-31"],
                "reportDate": ["2024-12-31"],
                "form": ["10-K"],
                "primaryDocument": ["integration-filing.htm"],
            },
            "files": [],
        },
    }
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))

        if str(request.url) == submissions_url:
            return httpx.Response(200, json=submissions_payload)

        if str(request.url) == filing_url:
            return httpx.Response(
                200,
                content=filing_content,
                headers={"Content-Type": "text/html"},
            )

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
            SecEdgarClient(
                settings,
                http_client=http_client,
            ) as sec_client,
        ):
            first_result = await ingest_company_filings(
                client=sec_client,
                session_factory=session_factory,
                cik=test_cik,
                forms={"10-K"},
                limit=1,
            )
            second_result = await ingest_company_filings(
                client=sec_client,
                session_factory=session_factory,
                cik=test_cik,
                forms={"10-K"},
                limit=1,
            )

        async with session_factory() as verification_session:
            company_result = await verification_session.execute(
                select(Company).where(Company.cik == test_cik)
            )
            company = company_result.scalar_one()

            filing_result = await verification_session.execute(
                select(Filing).where(Filing.accession_number == accession_number)
            )
            filing = filing_result.scalar_one()

            section_result = await verification_session.execute(
                select(FilingSection).where(FilingSection.filing_id == filing.id)
            )
            section = section_result.scalar_one()

            chunk_result = await verification_session.execute(
                select(FilingChunk).where(FilingChunk.section_id == section.id)
            )
            chunk = chunk_result.scalar_one()

        assert first_result.created_filings == 1
        assert first_result.created_sections == 1
        assert first_result.created_chunks == 1
        assert first_result.skipped_existing_filings == 0
        assert second_result.created_filings == 0
        assert second_result.created_sections == 0
        assert second_result.created_chunks == 0
        assert second_result.skipped_existing_filings == 1

        assert requested_urls.count(submissions_url) == 2
        assert requested_urls.count(filing_url) == 1

        assert company.legal_name == "FinSight SEC Integration Company"
        assert company.ticker == "FST"
        assert filing.company_id == company.id
        assert filing.source_url == filing_url
        assert filing.content_hash == hashlib.sha256(filing_content).hexdigest()
        assert filing.source_metadata == {
            "provider": "sec-edgar",
            "content_type": "text/html",
            "content_length": len(filing_content),
            "parser_version": "sec-html-v1",
            "tokenizer_name": "cl100k_base",
            "section_count": 1,
            "chunk_count": 1,
        }
        assert section.section_name == "Document"
        assert section.content == "Integration filing"
        assert section.source_metadata["parser_version"] == "sec-html-v1"
        assert chunk.content == "Integration filing"
        assert chunk.token_count > 0
        assert chunk.source_metadata["token_start"] == 0
    finally:
        async with session_scope(session_factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == test_cik))
        await engine.dispose()
