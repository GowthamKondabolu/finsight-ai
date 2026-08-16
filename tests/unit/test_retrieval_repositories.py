"""Tests for PostgreSQL keyword and semantic candidate retrieval."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from finsight.retrieval.repositories import (
    RetrievalFilters,
    search_keyword_chunks,
    search_semantic_chunks,
)


def candidate_row(raw_score: float = 0.75) -> SimpleNamespace:
    """Return one complete SQL result row double."""

    return SimpleNamespace(
        chunk_id=UUID("00000000-0000-4000-8000-000000000001"),
        content="Supply constraints may affect operations.",
        content_hash="a" * 64,
        chunk_index=2,
        chunk_metadata={"token_start": 10},
        section_id=UUID("00000000-0000-4000-8000-000000000002"),
        section_name="Item 1A. Risk Factors",
        section_sequence=1,
        filing_id=UUID("00000000-0000-4000-8000-000000000003"),
        accession_number="0000320193-25-000079",
        form_type="10-K",
        filing_date=date(2025, 10, 31),
        report_date=date(2025, 9, 27),
        source_url="https://www.sec.gov/example",
        company_id=UUID("00000000-0000-4000-8000-000000000004"),
        cik="0000320193",
        legal_name="Apple Inc.",
        ticker="AAPL",
        raw_score=raw_score,
    )


def result_session(raw_score: float = 0.75) -> AsyncMock:
    """Return a session whose next query produces one candidate."""

    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.all.return_value = [candidate_row(raw_score)]
    session.execute.return_value = result
    return session


@pytest.mark.asyncio
async def test_keyword_search_applies_every_metadata_filter() -> None:
    """Full-text retrieval must preserve identical bounded metadata semantics."""

    session = result_session()
    filters = RetrievalFilters(
        cik="0000320193",
        form_types=("10-K",),
        filed_from=date(2025, 1, 1),
        filed_to=date(2025, 12, 31),
        section_names=("Item 1A. Risk Factors",),
    )

    candidates = await search_keyword_chunks(
        session,
        query="supply constraints",
        filters=filters,
        limit=20,
    )

    assert candidates[0].raw_score == 0.75
    assert candidates[0].chunk_metadata == {"token_start": 10}
    statement = str(session.execute.await_args.args[0])
    assert "websearch_to_tsquery" in statement
    assert "companies.cik" in statement
    assert "filings.form_type IN" in statement
    assert "filings.filing_date >=" in statement
    assert "filings.filing_date <=" in statement
    assert "filing_sections.section_name IN" in statement


@pytest.mark.asyncio
async def test_semantic_search_uses_cosine_distance_and_model_filter() -> None:
    """Semantic retrieval should compare only vectors from the query model."""

    session = result_session(0.9)

    candidates = await search_semantic_chunks(
        session,
        query_embedding=(1.0, 0.0),
        model="test-model",
        filters=RetrievalFilters(),
        limit=10,
    )

    assert candidates[0].raw_score == 0.9
    statement = str(session.execute.await_args.args[0])
    assert "<=>" in statement
    assert "filing_chunks.embedding_model" in statement


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel", "limit", "embedding", "model", "message"),
    [
        ("keyword", 0, (1.0,), "model", "keyword"),
        ("semantic", 0, (1.0,), "model", "semantic"),
        ("semantic", 1, (), "model", "cannot be empty"),
        ("semantic", 1, (1.0,), " ", "model"),
    ],
)
async def test_candidate_search_rejects_invalid_bounds(
    channel: str,
    limit: int,
    embedding: tuple[float, ...],
    model: str,
    message: str,
) -> None:
    """Repository APIs should reject unbounded or incomplete requests."""

    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match=message):
        if channel == "keyword":
            await search_keyword_chunks(
                session,
                query="risk",
                filters=RetrievalFilters(),
                limit=limit,
            )
        else:
            await search_semantic_chunks(
                session,
                query_embedding=embedding,
                model=model,
                filters=RetrievalFilters(),
                limit=limit,
            )
