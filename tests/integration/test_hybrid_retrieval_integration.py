"""PostgreSQL integration tests for full-text and pgvector fusion."""

import os
from collections.abc import Sequence
from datetime import date

import pytest
from sqlalchemy import delete

from finsight.config.settings import Settings
from finsight.retrieval.search import RetrievalQuery, hybrid_search
from finsight.storage.database import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from finsight.storage.models import Company, Filing, FilingChunk, FilingSection

RUN_DATABASE_TESTS = os.getenv("FINSIGHT_RUN_DATABASE_TESTS") == "1"
TEST_CIK = "0000000043"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason="set FINSIGHT_RUN_DATABASE_TESTS=1 to run database integration tests",
    ),
]


class QueryProvider:
    """Deterministic semantic query provider for database integration."""

    model_name = "integration-search-model"
    dimensions = 1536

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Represent every query near the first test passage."""

        return [[1.0, *([0.0] * (self.dimensions - 1))] for _ in texts]


@pytest.mark.asyncio
async def test_hybrid_search_fuses_real_postgres_rankings_and_filters() -> None:
    """Keyword and cosine candidates should fuse into citation-complete results."""

    engine = create_database_engine(Settings())
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == TEST_CIK))

        async with session_scope(factory) as setup_session:
            company = Company(
                cik=TEST_CIK,
                legal_name="Hybrid Retrieval Test Company",
                ticker="HYBRID",
            )
            filing = Filing(
                accession_number="0000000043-26-000001",
                form_type="10-K",
                filing_date=date(2026, 2, 1),
                report_date=date(2025, 12, 31),
                primary_document="hybrid.htm",
                source_url="https://example.invalid/hybrid.htm",
                content_hash="1" * 64,
            )
            section = FilingSection(
                section_name="Item 1A. Risk Factors",
                sequence_number=0,
                content="Supply constraints and commodity prices may affect operations.",
                content_hash="2" * 64,
                char_count=63,
            )
            section.chunks = [
                FilingChunk(
                    chunk_index=0,
                    content="Supply constraints may materially affect operations.",
                    content_hash="3" * 64,
                    token_count=8,
                    embedding=[1.0, *([0.0] * 1535)],
                    embedding_model=QueryProvider.model_name,
                    source_metadata={"token_start": 0},
                ),
                FilingChunk(
                    chunk_index=1,
                    content="Commodity prices can change production costs.",
                    content_hash="4" * 64,
                    token_count=7,
                    embedding=[0.0, 1.0, *([0.0] * 1534)],
                    embedding_model=QueryProvider.model_name,
                    source_metadata={"token_start": 8},
                ),
            ]
            filing.sections = [section]
            company.filings = [filing]
            setup_session.add(company)

        results = await hybrid_search(
            query=RetrievalQuery(
                text="supply constraints",
                top_k=2,
                candidate_k=5,
                cik=TEST_CIK,
                form_types=("10-K",),
                filed_from=date(2026, 1, 1),
                filed_to=date(2026, 12, 31),
                section_names=("Item 1A. Risk Factors",),
            ),
            provider=QueryProvider(),
            session_factory=factory,
        )
        excluded = await hybrid_search(
            query=RetrievalQuery(
                text="supply constraints",
                top_k=2,
                candidate_k=5,
                cik=TEST_CIK,
                form_types=("10-Q",),
            ),
            provider=QueryProvider(),
            session_factory=factory,
        )

        assert results[0].content.startswith("Supply constraints")
        assert results[0].matched_by == ("keyword", "semantic")
        assert results[0].citation.cik == TEST_CIK
        assert results[0].citation.accession_number == "0000000043-26-000001"
        assert results[0].citation.source_url == "https://example.invalid/hybrid.htm"
        assert results[0].chunk_metadata == {"token_start": 0}
        assert excluded == []
    finally:
        async with session_scope(factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == TEST_CIK))
        await engine.dispose()
