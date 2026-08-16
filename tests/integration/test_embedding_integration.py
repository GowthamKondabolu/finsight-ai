"""PostgreSQL integration tests for idempotent embedding persistence."""

import os
from collections.abc import Sequence
from datetime import date

import pytest
from sqlalchemy import delete, select

from finsight.config.settings import Settings
from finsight.retrieval.embedding_service import embed_pending_chunks
from finsight.storage.database import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from finsight.storage.models import Company, Filing, FilingChunk, FilingSection

RUN_DATABASE_TESTS = os.getenv("FINSIGHT_RUN_DATABASE_TESTS") == "1"
TEST_CIK = "0000000042"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason="set FINSIGHT_RUN_DATABASE_TESTS=1 to run database integration tests",
    ),
]


class DeterministicEmbeddingProvider:
    """Local provider used to prove persistence without external API access."""

    model_name = "integration-embedding-model"
    dimensions = 1536

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one finite vector per input text."""

        return [
            [float(index + 1), *([0.0] * (self.dimensions - 1))] for index, _ in enumerate(texts)
        ]


@pytest.mark.asyncio
async def test_embedding_backfill_persists_vectors_idempotently() -> None:
    """A second run should observe no missing or stale chunk vectors."""

    engine = create_database_engine(Settings())
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == TEST_CIK))

        async with session_scope(factory) as setup_session:
            company = Company(cik=TEST_CIK, legal_name="Embedding Test Company")
            filing = Filing(
                accession_number="0000000042-26-000001",
                form_type="10-K",
                filing_date=date(2026, 1, 1),
                primary_document="test.htm",
                source_url="https://example.invalid/test.htm",
                content_hash="a" * 64,
            )
            section = FilingSection(
                section_name="Item 1A. Risk Factors",
                sequence_number=0,
                content="Risk factors include supply constraints and market volatility.",
                content_hash="b" * 64,
                char_count=63,
            )
            section.chunks = [
                FilingChunk(
                    chunk_index=0,
                    content="Supply constraints may affect operations.",
                    content_hash="c" * 64,
                    token_count=7,
                ),
                FilingChunk(
                    chunk_index=1,
                    content="Market volatility may affect demand.",
                    content_hash="d" * 64,
                    token_count=6,
                ),
            ]
            filing.sections = [section]
            company.filings = [filing]
            setup_session.add(company)

        provider = DeterministicEmbeddingProvider()
        first = await embed_pending_chunks(
            provider=provider,
            session_factory=factory,
            limit=10,
            batch_size=2,
            cik=TEST_CIK,
        )
        second = await embed_pending_chunks(
            provider=provider,
            session_factory=factory,
            limit=10,
            batch_size=2,
            cik=TEST_CIK,
        )

        async with factory() as verification_session:
            result = await verification_session.execute(
                select(FilingChunk)
                .join(FilingSection)
                .join(Filing)
                .join(Company)
                .where(Company.cik == TEST_CIK)
                .order_by(FilingChunk.chunk_index)
            )
            chunks = list(result.scalars().all())

        assert first.embedded_chunks == 2
        assert second.embedded_chunks == 0
        assert [chunk.embedding_model for chunk in chunks] == [
            provider.model_name,
            provider.model_name,
        ]
        assert all(chunk.embedding is not None for chunk in chunks)
        assert all(len(chunk.embedding or []) == provider.dimensions for chunk in chunks)
    finally:
        async with session_scope(factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == TEST_CIK))
        await engine.dispose()
