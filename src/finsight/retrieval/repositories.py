"""PostgreSQL full-text and pgvector candidate retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from finsight.storage.models import Company, Filing, FilingChunk, FilingSection


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """Normalized metadata constraints shared by both retrieval channels."""

    cik: str | None = None
    form_types: tuple[str, ...] = ()
    filed_from: date | None = None
    filed_to: date | None = None
    section_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    """One ranked chunk with complete source and citation metadata."""

    chunk_id: UUID
    content: str
    content_hash: str
    chunk_index: int
    chunk_metadata: dict[str, Any]
    section_id: UUID
    section_name: str
    section_sequence: int
    filing_id: UUID
    accession_number: str
    form_type: str
    filing_date: date
    report_date: date | None
    source_url: str
    company_id: UUID
    cik: str
    legal_name: str
    ticker: str | None
    raw_score: float


def _candidate_statement(score: Any) -> Select[Any]:
    """Build the shared citation-preserving candidate projection."""

    return (
        select(
            FilingChunk.id.label("chunk_id"),
            FilingChunk.content,
            FilingChunk.content_hash,
            FilingChunk.chunk_index,
            FilingChunk.source_metadata.label("chunk_metadata"),
            FilingSection.id.label("section_id"),
            FilingSection.section_name,
            FilingSection.sequence_number.label("section_sequence"),
            Filing.id.label("filing_id"),
            Filing.accession_number,
            Filing.form_type,
            Filing.filing_date,
            Filing.report_date,
            Filing.source_url,
            Company.id.label("company_id"),
            Company.cik,
            Company.legal_name,
            Company.ticker,
            score.label("raw_score"),
        )
        .join(FilingSection, FilingChunk.section_id == FilingSection.id)
        .join(Filing, FilingSection.filing_id == Filing.id)
        .join(Company, Filing.company_id == Company.id)
    )


def _apply_filters(statement: Select[Any], filters: RetrievalFilters) -> Select[Any]:
    """Apply identical metadata constraints to keyword and semantic channels."""

    if filters.cik is not None:
        statement = statement.where(Company.cik == filters.cik)
    if filters.form_types:
        statement = statement.where(Filing.form_type.in_(filters.form_types))
    if filters.filed_from is not None:
        statement = statement.where(Filing.filing_date >= filters.filed_from)
    if filters.filed_to is not None:
        statement = statement.where(Filing.filing_date <= filters.filed_to)
    if filters.section_names:
        statement = statement.where(FilingSection.section_name.in_(filters.section_names))
    return statement


def _to_candidates(rows: list[Any]) -> list[SearchCandidate]:
    """Convert SQLAlchemy result rows into immutable channel candidates."""

    return [
        SearchCandidate(
            chunk_id=row.chunk_id,
            content=row.content,
            content_hash=row.content_hash,
            chunk_index=row.chunk_index,
            chunk_metadata=dict(row.chunk_metadata),
            section_id=row.section_id,
            section_name=row.section_name,
            section_sequence=row.section_sequence,
            filing_id=row.filing_id,
            accession_number=row.accession_number,
            form_type=row.form_type,
            filing_date=row.filing_date,
            report_date=row.report_date,
            source_url=row.source_url,
            company_id=row.company_id,
            cik=row.cik,
            legal_name=row.legal_name,
            ticker=row.ticker,
            raw_score=float(row.raw_score),
        )
        for row in rows
    ]


async def search_keyword_chunks(
    session: AsyncSession,
    *,
    query: str,
    filters: RetrievalFilters,
    limit: int,
) -> list[SearchCandidate]:
    """Rank filing chunks with PostgreSQL English full-text search."""

    if limit < 1:
        raise ValueError("keyword candidate limit must be positive")

    ts_query = func.websearch_to_tsquery("english", query)
    score = func.ts_rank_cd(FilingChunk.search_vector, ts_query)
    statement = _candidate_statement(score).where(FilingChunk.search_vector.op("@@")(ts_query))
    statement = _apply_filters(statement, filters).order_by(
        score.desc(),
        FilingChunk.id,
    )
    result = await session.execute(statement.limit(limit))
    return _to_candidates(list(result.all()))


async def search_semantic_chunks(
    session: AsyncSession,
    *,
    query_embedding: tuple[float, ...],
    model: str,
    filters: RetrievalFilters,
    limit: int,
) -> list[SearchCandidate]:
    """Rank embedded chunks by pgvector cosine similarity."""

    if limit < 1:
        raise ValueError("semantic candidate limit must be positive")
    if not query_embedding:
        raise ValueError("query embedding cannot be empty")
    if not model.strip():
        raise ValueError("embedding model cannot be blank")

    distance = FilingChunk.embedding.cosine_distance(list(query_embedding))
    score = 1.0 - distance
    statement = _candidate_statement(score).where(
        FilingChunk.embedding.is_not(None),
        FilingChunk.embedding_model == model,
    )
    statement = _apply_filters(statement, filters).order_by(
        distance.asc(),
        FilingChunk.id,
    )
    result = await session.execute(statement.limit(limit))
    return _to_candidates(list(result.all()))
