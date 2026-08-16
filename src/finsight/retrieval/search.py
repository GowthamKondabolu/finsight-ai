"""Hybrid retrieval and transparent weighted reciprocal-rank fusion."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from uuid import UUID

from finsight.ingestion.sec_schemas import normalize_cik
from finsight.retrieval.embeddings import EmbeddingProvider
from finsight.retrieval.repositories import (
    RetrievalFilters,
    SearchCandidate,
    search_keyword_chunks,
    search_semantic_chunks,
)
from finsight.storage.database import SessionFactory

MAX_RETRIEVAL_RESULTS = 50
MAX_RETRIEVAL_CANDIDATES = 200
DEFAULT_RRF_K = 60


class RetrievalContractError(RuntimeError):
    """Raised when retrieval channels disagree about immutable source content."""


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """Validated hybrid-search request independent of the delivery surface."""

    text: str
    top_k: int = 10
    candidate_k: int = 50
    cik: str | None = None
    form_types: tuple[str, ...] = ()
    filed_from: date | None = None
    filed_to: date | None = None
    section_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalCitation:
    """Stable source location returned with every retrieved passage."""

    company_name: str
    cik: str
    ticker: str | None
    accession_number: str
    form_type: str
    filing_date: date
    report_date: date | None
    section_name: str
    section_sequence: int
    chunk_index: int
    source_url: str


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """One fused result with channel ranks, scores, and citation metadata."""

    chunk_id: UUID
    content: str
    content_hash: str
    score: float
    keyword_rank: int | None
    semantic_rank: int | None
    keyword_score: float | None
    semantic_score: float | None
    matched_by: tuple[str, ...]
    citation: RetrievalCitation
    chunk_metadata: dict[str, object]


@dataclass(slots=True)
class _FusionEntry:
    candidate: SearchCandidate
    keyword_rank: int | None = None
    semantic_rank: int | None = None
    keyword_score: float | None = None
    semantic_score: float | None = None


def _normalize_query(query: RetrievalQuery) -> RetrievalQuery:
    """Normalize and validate public query controls before provider or SQL use."""

    text = query.text.strip()
    if not text:
        raise ValueError("retrieval query cannot be blank")
    if not 1 <= query.top_k <= MAX_RETRIEVAL_RESULTS:
        raise ValueError(f"top_k must be between 1 and {MAX_RETRIEVAL_RESULTS}")
    if not query.top_k <= query.candidate_k <= MAX_RETRIEVAL_CANDIDATES:
        raise ValueError(f"candidate_k must be between top_k and {MAX_RETRIEVAL_CANDIDATES}")
    if (
        query.filed_from is not None
        and query.filed_to is not None
        and query.filed_from > query.filed_to
    ):
        raise ValueError("filed_from cannot be after filed_to")

    forms = tuple(sorted({value.strip().upper() for value in query.form_types if value.strip()}))
    sections = tuple(sorted({value.strip() for value in query.section_names if value.strip()}))
    if len(forms) != len(query.form_types):
        raise ValueError("form_types cannot contain blank or duplicate values")
    if len(sections) != len(query.section_names):
        raise ValueError("section_names cannot contain blank or duplicate values")

    return replace(
        query,
        text=text,
        cik=normalize_cik(query.cik) if query.cik is not None else None,
        form_types=forms,
        section_names=sections,
    )


def _add_channel(
    entries: dict[UUID, _FusionEntry],
    candidates: list[SearchCandidate],
    *,
    channel: str,
) -> None:
    """Attach one ranked channel while enforcing immutable-source agreement."""

    for rank, candidate in enumerate(candidates, start=1):
        entry = entries.get(candidate.chunk_id)
        if entry is None:
            entry = _FusionEntry(candidate=candidate)
            entries[candidate.chunk_id] = entry
        elif entry.candidate.content_hash != candidate.content_hash:
            raise RetrievalContractError(
                "retrieval channels returned different content for the same chunk"
            )

        if channel == "keyword":
            entry.keyword_rank = rank
            entry.keyword_score = candidate.raw_score
        else:
            entry.semantic_rank = rank
            entry.semantic_score = candidate.raw_score


def _fuse_results(
    keyword_candidates: list[SearchCandidate],
    semantic_candidates: list[SearchCandidate],
    *,
    top_k: int,
    rrf_k: int,
    keyword_weight: float,
    semantic_weight: float,
) -> list[HybridSearchResult]:
    """Rerank independent channels with weighted reciprocal-rank fusion."""

    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")
    if keyword_weight < 0.0 or semantic_weight < 0.0:
        raise ValueError("retrieval channel weights cannot be negative")
    if keyword_weight == 0.0 and semantic_weight == 0.0:
        raise ValueError("at least one retrieval channel weight must be positive")

    entries: dict[UUID, _FusionEntry] = {}
    _add_channel(entries, keyword_candidates, channel="keyword")
    _add_channel(entries, semantic_candidates, channel="semantic")

    scored: list[tuple[float, _FusionEntry]] = []
    for entry in entries.values():
        score = 0.0
        if entry.keyword_rank is not None:
            score += keyword_weight / (rrf_k + entry.keyword_rank)
        if entry.semantic_rank is not None:
            score += semantic_weight / (rrf_k + entry.semantic_rank)
        scored.append((score, entry))

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].semantic_rank or MAX_RETRIEVAL_CANDIDATES + 1,
            item[1].keyword_rank or MAX_RETRIEVAL_CANDIDATES + 1,
            str(item[1].candidate.chunk_id),
        )
    )

    results: list[HybridSearchResult] = []
    for score, entry in scored[:top_k]:
        candidate = entry.candidate
        matched_by = tuple(
            channel
            for channel, rank in (
                ("keyword", entry.keyword_rank),
                ("semantic", entry.semantic_rank),
            )
            if rank is not None
        )
        results.append(
            HybridSearchResult(
                chunk_id=candidate.chunk_id,
                content=candidate.content,
                content_hash=candidate.content_hash,
                score=score,
                keyword_rank=entry.keyword_rank,
                semantic_rank=entry.semantic_rank,
                keyword_score=entry.keyword_score,
                semantic_score=entry.semantic_score,
                matched_by=matched_by,
                citation=RetrievalCitation(
                    company_name=candidate.legal_name,
                    cik=candidate.cik,
                    ticker=candidate.ticker,
                    accession_number=candidate.accession_number,
                    form_type=candidate.form_type,
                    filing_date=candidate.filing_date,
                    report_date=candidate.report_date,
                    section_name=candidate.section_name,
                    section_sequence=candidate.section_sequence,
                    chunk_index=candidate.chunk_index,
                    source_url=candidate.source_url,
                ),
                chunk_metadata=dict(candidate.chunk_metadata),
            )
        )
    return results


async def hybrid_search(
    *,
    query: RetrievalQuery,
    provider: EmbeddingProvider,
    session_factory: SessionFactory,
    rrf_k: int = DEFAULT_RRF_K,
    keyword_weight: float = 1.0,
    semantic_weight: float = 1.0,
) -> list[HybridSearchResult]:
    """Retrieve independent candidates and return transparent fused rankings."""

    normalized = _normalize_query(query)
    query_vectors = await provider.embed([normalized.text])
    if len(query_vectors) != 1 or len(query_vectors[0]) != provider.dimensions:
        raise RetrievalContractError("embedding provider returned an invalid query vector")

    filters = RetrievalFilters(
        cik=normalized.cik,
        form_types=normalized.form_types,
        filed_from=normalized.filed_from,
        filed_to=normalized.filed_to,
        section_names=normalized.section_names,
    )
    async with session_factory() as session:
        keyword_candidates = await search_keyword_chunks(
            session,
            query=normalized.text,
            filters=filters,
            limit=normalized.candidate_k,
        )
        semantic_candidates = await search_semantic_chunks(
            session,
            query_embedding=tuple(query_vectors[0]),
            model=provider.model_name,
            filters=filters,
            limit=normalized.candidate_k,
        )

    return _fuse_results(
        keyword_candidates,
        semantic_candidates,
        top_k=normalized.top_k,
        rrf_k=rrf_k,
        keyword_weight=keyword_weight,
        semantic_weight=semantic_weight,
    )
