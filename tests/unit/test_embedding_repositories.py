"""Tests for filing-chunk embedding persistence operations."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from finsight.storage.repositories import (
    ChunkEmbeddingUpdate,
    list_chunks_for_embedding,
    store_chunk_embeddings,
)

CHUNK_ID = UUID("00000000-0000-4000-8000-000000000001")


def update_command(
    *,
    chunk_id: UUID = CHUNK_ID,
    embedding: tuple[float, ...] = (1.0, 0.0),
) -> ChunkEmbeddingUpdate:
    """Create one optimistic vector update."""

    return ChunkEmbeddingUpdate(
        chunk_id=chunk_id,
        content_hash="a" * 64,
        embedding=embedding,
    )


@pytest.mark.asyncio
async def test_list_chunks_requires_positive_limit() -> None:
    """Repository selection should remain explicitly bounded."""

    with pytest.raises(ValueError, match="positive"):
        await list_chunks_for_embedding(
            AsyncMock(spec=AsyncSession),
            model="model",
            limit=0,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cik", [None, "0000320193"])
async def test_list_chunks_returns_stable_inputs_with_optional_company_filter(
    cik: str | None,
) -> None:
    """Embedding selection should preserve IDs, source hashes, and issuer filters."""

    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.all.return_value = [
        SimpleNamespace(
            id=CHUNK_ID,
            content="Risk factors changed.",
            content_hash="a" * 64,
        )
    ]
    session.execute.return_value = result

    chunks = await list_chunks_for_embedding(
        session,
        model="test-model",
        limit=5,
        cik=cik,
    )

    assert chunks[0].chunk_id == CHUNK_ID
    assert chunks[0].content == "Risk factors changed."
    assert chunks[0].content_hash == "a" * 64
    statement = str(session.execute.await_args.args[0])
    assert "filing_chunks.embedding IS NULL" in statement
    assert ("JOIN companies" in statement) is (cik is not None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "dimensions", "updates", "message"),
    [
        (" ", 2, [], "model"),
        ("model", 0, [], "dimensions"),
        (
            "model",
            2,
            [update_command(), update_command()],
            "unique",
        ),
        ("model", 2, [update_command(embedding=(1.0,))], "dimensions"),
        ("model", 2, [update_command(embedding=(float("inf"), 0.0))], "non-finite"),
    ],
)
async def test_store_embeddings_rejects_invalid_contracts(
    model: str,
    dimensions: int,
    updates: list[ChunkEmbeddingUpdate],
    message: str,
) -> None:
    """Invalid vectors and ambiguous writes must fail before persistence."""

    with pytest.raises(ValueError, match=message):
        await store_chunk_embeddings(
            AsyncMock(spec=AsyncSession),
            model=model,
            dimensions=dimensions,
            updates=updates,
        )


@pytest.mark.asyncio
async def test_store_embeddings_reports_optimistic_matches() -> None:
    """Only rows with the selected source hash should count as persisted."""

    session = AsyncMock(spec=AsyncSession)
    first_result = Mock()
    first_result.scalar_one_or_none.return_value = CHUNK_ID
    second_result = Mock()
    second_result.scalar_one_or_none.return_value = None
    session.execute.side_effect = [first_result, second_result]
    second_id = UUID("00000000-0000-4000-8000-000000000002")

    stored = await store_chunk_embeddings(
        session,
        model="test-model",
        dimensions=2,
        updates=[update_command(), update_command(chunk_id=second_id)],
    )

    assert stored == 1
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_store_embeddings_accepts_empty_batch() -> None:
    """A valid empty batch should avoid database work."""

    session = AsyncMock(spec=AsyncSession)

    assert (
        await store_chunk_embeddings(
            session,
            model="model",
            dimensions=2,
            updates=[],
        )
        == 0
    )
    session.execute.assert_not_awaited()
