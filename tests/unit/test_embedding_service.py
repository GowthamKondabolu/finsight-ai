"""Tests for bounded and transaction-aware chunk embedding orchestration."""

from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import finsight.retrieval.embedding_service as service_module
from finsight.retrieval.embedding_service import (
    EmbeddingPersistenceError,
    embed_pending_chunks,
)
from finsight.retrieval.embeddings import EmbeddingProvider
from finsight.storage.database import SessionFactory
from finsight.storage.repositories import PendingChunkEmbedding


def chunk(number: int) -> PendingChunkEmbedding:
    """Create one deterministic pending chunk."""

    return PendingChunkEmbedding(
        chunk_id=UUID(f"00000000-0000-4000-8000-{number:012d}"),
        content=f"chunk {number}",
        content_hash=f"{number:064x}",
    )


def provider() -> EmbeddingProvider:
    """Return a typed embedding provider double."""

    instance = Mock()
    instance.model_name = "test-model"
    instance.dimensions = 2
    instance.embed = AsyncMock()
    return cast(EmbeddingProvider, instance)


def session_factory() -> tuple[SessionFactory, AsyncMock]:
    """Return a reusable asynchronous session factory double."""

    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    factory = Mock(return_value=session)
    return cast(SessionFactory, factory), session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "batch_size", "message"),
    [
        (0, 1, "limit"),
        (10_001, 1, "limit"),
        (2, 0, "batch size"),
        (2, 3, "batch size"),
    ],
)
async def test_service_rejects_invalid_bounds(
    limit: int,
    batch_size: int,
    message: str,
) -> None:
    """Every embedding run must have an explicit bounded workload."""

    with pytest.raises(ValueError, match=message):
        await embed_pending_chunks(
            provider=provider(),
            session_factory=cast(SessionFactory, Mock()),
            limit=limit,
            batch_size=batch_size,
        )


@pytest.mark.asyncio
async def test_service_embeds_multiple_batches_and_normalizes_cik(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service should keep API calls outside short write transactions."""

    embedding_provider = provider()
    cast(AsyncMock, embedding_provider.embed).side_effect = [
        [[1.0, 0.0], [0.0, 1.0]],
        [[0.5, 0.5]],
    ]
    factory, session = session_factory()
    list_mock = AsyncMock(side_effect=[[chunk(1), chunk(2)], [chunk(3)]])
    store_mock = AsyncMock(side_effect=[2, 1])
    monkeypatch.setattr(service_module, "list_chunks_for_embedding", list_mock)
    monkeypatch.setattr(service_module, "store_chunk_embeddings", store_mock)

    result = await embed_pending_chunks(
        provider=embedding_provider,
        session_factory=factory,
        limit=3,
        batch_size=2,
        cik="320193",
    )

    assert result.model == "test-model"
    assert result.dimensions == 2
    assert result.selected_chunks == 3
    assert result.embedded_chunks == 3
    assert result.cik == "0000320193"
    assert list_mock.await_count == 2
    assert list_mock.await_args_list[0].kwargs == {
        "model": "test-model",
        "limit": 2,
        "cik": "0000320193",
    }
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_service_stops_when_no_chunks_are_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An up-to-date index should complete without provider usage."""

    embedding_provider = provider()
    factory, _ = session_factory()
    monkeypatch.setattr(
        service_module,
        "list_chunks_for_embedding",
        AsyncMock(return_value=[]),
    )
    store_mock = AsyncMock()
    monkeypatch.setattr(service_module, "store_chunk_embeddings", store_mock)

    result = await embed_pending_chunks(
        provider=embedding_provider,
        session_factory=factory,
        limit=2,
        batch_size=2,
    )

    assert result.embedded_chunks == 0
    cast(AsyncMock, embedding_provider.embed).assert_not_awaited()
    store_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_rejects_provider_cardinality_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial provider response must not be paired to the wrong chunks."""

    embedding_provider = provider()
    cast(AsyncMock, embedding_provider.embed).return_value = [[1.0, 0.0]]
    factory, _ = session_factory()
    monkeypatch.setattr(
        service_module,
        "list_chunks_for_embedding",
        AsyncMock(return_value=[chunk(1), chunk(2)]),
    )

    with pytest.raises(EmbeddingPersistenceError, match="different number"):
        await embed_pending_chunks(
            provider=embedding_provider,
            session_factory=factory,
            limit=2,
            batch_size=2,
        )


@pytest.mark.asyncio
async def test_service_detects_concurrent_source_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optimistic hash conflicts should require an explicit retry."""

    embedding_provider = provider()
    cast(AsyncMock, embedding_provider.embed).return_value = [[1.0, 0.0]]
    factory, _ = session_factory()
    monkeypatch.setattr(
        service_module,
        "list_chunks_for_embedding",
        AsyncMock(return_value=[chunk(1)]),
    )
    monkeypatch.setattr(
        service_module,
        "store_chunk_embeddings",
        AsyncMock(return_value=0),
    )

    with pytest.raises(EmbeddingPersistenceError, match="changed"):
        await embed_pending_chunks(
            provider=embedding_provider,
            session_factory=factory,
            limit=1,
            batch_size=1,
        )
