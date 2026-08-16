"""Tests for metadata-aware weighted reciprocal-rank fusion."""

from datetime import date
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import finsight.retrieval.search as search_module
from finsight.retrieval.embeddings import EmbeddingProvider
from finsight.retrieval.repositories import SearchCandidate
from finsight.retrieval.search import (
    RetrievalContractError,
    RetrievalQuery,
    hybrid_search,
)
from finsight.storage.database import SessionFactory


def candidate(number: int, score: float = 0.8, content_hash: str | None = None) -> SearchCandidate:
    """Return one citation-complete channel candidate."""

    return SearchCandidate(
        chunk_id=UUID(f"00000000-0000-4000-8000-{number:012d}"),
        content=f"risk passage {number}",
        content_hash=content_hash or f"{number:064x}",
        chunk_index=number,
        chunk_metadata={"token_start": number * 10},
        section_id=UUID(f"10000000-0000-4000-8000-{number:012d}"),
        section_name="Item 1A. Risk Factors",
        section_sequence=1,
        filing_id=UUID(f"20000000-0000-4000-8000-{number:012d}"),
        accession_number=f"0000000001-26-{number:06d}",
        form_type="10-K",
        filing_date=date(2026, 1, number),
        report_date=date(2025, 12, 31),
        source_url=f"https://example.invalid/{number}",
        company_id=UUID(f"30000000-0000-4000-8000-{number:012d}"),
        cik="0000320193",
        legal_name="Apple Inc.",
        ticker="AAPL",
        raw_score=score,
    )


def provider(vectors: list[list[float]] | None = None) -> EmbeddingProvider:
    """Return a typed query embedding provider double."""

    instance = Mock()
    instance.model_name = "test-model"
    instance.dimensions = 2
    instance.embed = AsyncMock(return_value=vectors if vectors is not None else [[1.0, 0.0]])
    return cast(EmbeddingProvider, instance)


def session_factory() -> SessionFactory:
    """Return one asynchronous read-session factory double."""

    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    return cast(SessionFactory, Mock(return_value=session))


@pytest.mark.asyncio
async def test_hybrid_search_normalizes_filters_and_fuses_both_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chunk present in both channels should outrank single-channel results."""

    a, b, c = candidate(1), candidate(2), candidate(3)
    keyword = AsyncMock(return_value=[a, b])
    semantic = AsyncMock(return_value=[b, c])
    monkeypatch.setattr(search_module, "search_keyword_chunks", keyword)
    monkeypatch.setattr(search_module, "search_semantic_chunks", semantic)
    embedding_provider = provider()

    results = await hybrid_search(
        query=RetrievalQuery(
            text="  changed risks  ",
            top_k=3,
            candidate_k=5,
            cik="320193",
            form_types=("10-k",),
            filed_from=date(2025, 1, 1),
            filed_to=date(2026, 1, 31),
            section_names=("Item 1A. Risk Factors",),
        ),
        provider=embedding_provider,
        session_factory=session_factory(),
    )

    assert [result.chunk_id for result in results] == [b.chunk_id, a.chunk_id, c.chunk_id]
    assert results[0].matched_by == ("keyword", "semantic")
    assert results[0].keyword_rank == 2
    assert results[0].semantic_rank == 1
    assert results[0].citation.accession_number == b.accession_number
    assert results[0].chunk_metadata == {"token_start": 20}
    cast(AsyncMock, embedding_provider.embed).assert_awaited_once_with(["changed risks"])
    assert keyword.await_args is not None
    filters = keyword.await_args.kwargs["filters"]
    assert filters.cik == "0000320193"
    assert filters.form_types == ("10-K",)
    search_session = keyword.await_args.args[0]
    semantic.assert_awaited_once_with(
        search_session,
        query_embedding=(1.0, 0.0),
        model="test-model",
        filters=filters,
        limit=5,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "message"),
    [
        (RetrievalQuery(text=" "), "blank"),
        (RetrievalQuery(text="risk", top_k=0), "top_k"),
        (RetrievalQuery(text="risk", top_k=51, candidate_k=51), "top_k"),
        (RetrievalQuery(text="risk", top_k=5, candidate_k=4), "candidate_k"),
        (RetrievalQuery(text="risk", candidate_k=201), "candidate_k"),
        (
            RetrievalQuery(
                text="risk",
                filed_from=date(2026, 2, 1),
                filed_to=date(2026, 1, 1),
            ),
            "filed_from",
        ),
        (RetrievalQuery(text="risk", form_types=("10-K", "10-k")), "form_types"),
        (RetrievalQuery(text="risk", section_names=("Risk", "Risk")), "section_names"),
    ],
)
async def test_hybrid_search_rejects_invalid_query_controls(
    query: RetrievalQuery,
    message: str,
) -> None:
    """Invalid controls should fail before embedding or database work."""

    with pytest.raises(ValueError, match=message):
        await hybrid_search(
            query=query,
            provider=provider(),
            session_factory=cast(SessionFactory, Mock()),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("vectors", [[], [[1.0]], [[1.0, 0.0], [0.0, 1.0]]])
async def test_hybrid_search_rejects_invalid_query_vectors(
    vectors: list[list[float]],
) -> None:
    """Search must receive exactly one schema-compatible query vector."""

    with pytest.raises(RetrievalContractError, match="invalid query vector"):
        await hybrid_search(
            query=RetrievalQuery(text="risk"),
            provider=provider(vectors),
            session_factory=cast(SessionFactory, Mock()),
        )


def test_fusion_rejects_cross_channel_content_mismatch() -> None:
    """The same chunk ID must resolve to one immutable source hash."""

    first = candidate(1, content_hash="a" * 64)
    second = candidate(1, content_hash="b" * 64)

    with pytest.raises(RetrievalContractError, match="different content"):
        search_module._fuse_results(
            [first],
            [second],
            top_k=1,
            rrf_k=60,
            keyword_weight=1.0,
            semantic_weight=1.0,
        )


@pytest.mark.parametrize(
    ("rrf_k", "keyword_weight", "semantic_weight", "message"),
    [
        (0, 1.0, 1.0, "rrf_k"),
        (60, -1.0, 1.0, "negative"),
        (60, 1.0, -1.0, "negative"),
        (60, 0.0, 0.0, "at least one"),
    ],
)
def test_fusion_rejects_invalid_ranking_configuration(
    rrf_k: int,
    keyword_weight: float,
    semantic_weight: float,
    message: str,
) -> None:
    """Fusion weights must define a positive, stable ranking."""

    with pytest.raises(ValueError, match=message):
        search_module._fuse_results(
            [],
            [],
            top_k=1,
            rrf_k=rrf_k,
            keyword_weight=keyword_weight,
            semantic_weight=semantic_weight,
        )
