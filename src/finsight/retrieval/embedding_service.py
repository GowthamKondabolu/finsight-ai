"""Bounded, idempotent embedding orchestration for persisted filing chunks."""

from __future__ import annotations

from dataclasses import dataclass

from finsight.ingestion.sec_schemas import normalize_cik
from finsight.retrieval.embeddings import EmbeddingProvider
from finsight.storage.database import SessionFactory, session_scope
from finsight.storage.repositories import (
    ChunkEmbeddingUpdate,
    list_chunks_for_embedding,
    store_chunk_embeddings,
)

MAX_EMBEDDING_CHUNKS_PER_RUN = 10_000


class EmbeddingPersistenceError(RuntimeError):
    """Raised when source content changes between embedding and persistence."""


@dataclass(frozen=True, slots=True)
class EmbeddingRunResult:
    """Observable result of one bounded embedding backfill run."""

    model: str
    dimensions: int
    selected_chunks: int
    embedded_chunks: int
    cik: str | None


async def embed_pending_chunks(
    *,
    provider: EmbeddingProvider,
    session_factory: SessionFactory,
    limit: int = 500,
    batch_size: int = 100,
    cik: str | None = None,
) -> EmbeddingRunResult:
    """Embed missing or stale chunk vectors without holding network transactions."""

    if not 1 <= limit <= MAX_EMBEDDING_CHUNKS_PER_RUN:
        raise ValueError(f"embedding limit must be between 1 and {MAX_EMBEDDING_CHUNKS_PER_RUN}")
    if not 1 <= batch_size <= limit:
        raise ValueError("embedding batch size must be between 1 and the run limit")

    normalized_cik = normalize_cik(cik) if cik is not None else None
    embedded_count = 0

    while embedded_count < limit:
        current_batch_size = min(batch_size, limit - embedded_count)
        async with session_factory() as read_session:
            chunks = await list_chunks_for_embedding(
                read_session,
                model=provider.model_name,
                limit=current_batch_size,
                cik=normalized_cik,
            )

        if not chunks:
            break

        vectors = await provider.embed([chunk.content for chunk in chunks])
        if len(vectors) != len(chunks):
            raise EmbeddingPersistenceError(
                "embedding provider returned a different number of vectors than inputs"
            )

        updates = [
            ChunkEmbeddingUpdate(
                chunk_id=chunk.chunk_id,
                content_hash=chunk.content_hash,
                embedding=tuple(vector),
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        async with session_scope(session_factory) as write_session:
            stored_count = await store_chunk_embeddings(
                write_session,
                model=provider.model_name,
                dimensions=provider.dimensions,
                updates=updates,
            )

        if stored_count != len(updates):
            raise EmbeddingPersistenceError(
                "one or more chunks changed while embeddings were generated; retry the run"
            )

        embedded_count += stored_count

    return EmbeddingRunResult(
        model=provider.model_name,
        dimensions=provider.dimensions,
        selected_chunks=embedded_count,
        embedded_chunks=embedded_count,
        cik=normalized_cik,
    )
