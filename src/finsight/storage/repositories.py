"""Idempotent persistence operations for SEC companies and filings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from finsight.storage.models import (
    Company,
    Filing,
    FilingChunk,
    FilingSection,
    FinancialFact,
)

MAX_FINANCIAL_FACTS_PER_INSERT = 1_000


class FilingPersistenceError(RuntimeError):
    """Base error for filing persistence failures."""


class FilingIdentityConflictError(FilingPersistenceError):
    """Raised when an accession number conflicts with persisted provenance."""


@dataclass(frozen=True, slots=True)
class CompanyUpsert:
    """Mutable company attributes discovered from SEC submissions."""

    cik: str
    legal_name: str
    ticker: str | None
    sic: str | None
    fiscal_year_end: str | None


@dataclass(frozen=True, slots=True)
class FilingCreate:
    """Immutable filing attributes required for persistence."""

    company_id: UUID
    accession_number: str
    form_type: str
    filing_date: date
    report_date: date | None
    primary_document: str
    source_url: str
    content_hash: str
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredFiling:
    """A persisted filing and whether this transaction created it."""

    filing: Filing
    created: bool


@dataclass(frozen=True, slots=True)
class FilingChunkCreate:
    """One deterministic retrieval chunk within a filing section."""

    chunk_index: int
    content: str
    content_hash: str
    token_count: int
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FilingSectionCreate:
    """One source-preserving section and its retrieval chunks."""

    section_name: str
    sequence_number: int
    content: str
    content_hash: str
    char_count: int
    chunks: tuple[FilingChunkCreate, ...] = ()
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredFilingContent:
    """Counts produced when section and chunk records are persisted."""

    section_count: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class FinancialFactCreate:
    """One normalized SEC XBRL observation ready for persistence."""

    observation_key: str
    taxonomy: str
    concept: str
    label: str
    description: str
    unit: str
    value: Decimal
    start_date: date | None
    end_date: date
    filed_date: date
    fiscal_year: int | None
    fiscal_period: str | None
    form_type: str
    accession_number: str
    frame: str | None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredFinancialFacts:
    """Counts produced by an idempotent company-facts insert."""

    created_count: int
    existing_count: int


async def upsert_company(
    session: AsyncSession,
    command: CompanyUpsert,
) -> Company:
    """Insert a company or refresh its mutable SEC attributes."""

    base_statement = insert(Company).values(
        cik=command.cik,
        legal_name=command.legal_name,
        ticker=command.ticker,
        sic=command.sic,
        fiscal_year_end=command.fiscal_year_end,
    )
    statement = base_statement.on_conflict_do_update(
        index_elements=[Company.cik],
        set_={
            "legal_name": base_statement.excluded.legal_name,
            "ticker": base_statement.excluded.ticker,
            "sic": base_statement.excluded.sic,
            "fiscal_year_end": base_statement.excluded.fiscal_year_end,
            "updated_at": func.now(),
        },
    ).returning(Company)

    result = await session.execute(statement)
    return result.scalar_one()


async def find_existing_accession_numbers(
    session: AsyncSession,
    accession_numbers: Sequence[str],
) -> set[str]:
    """Return accession numbers already persisted by a previous ingestion."""

    if not accession_numbers:
        return set()

    statement = select(Filing.accession_number).where(
        Filing.accession_number.in_(accession_numbers)
    )
    result = await session.execute(statement)
    return set(result.scalars().all())


async def get_filing_by_accession_number(
    session: AsyncSession,
    accession_number: str,
) -> Filing | None:
    """Return one filing by its globally unique SEC accession number."""

    statement = select(Filing).where(Filing.accession_number == accession_number)
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def store_filing(
    session: AsyncSession,
    command: FilingCreate,
) -> StoredFiling:
    """Insert an immutable filing or verify an existing identical record."""

    statement = (
        insert(Filing)
        .values(
            company_id=command.company_id,
            accession_number=command.accession_number,
            form_type=command.form_type,
            filing_date=command.filing_date,
            report_date=command.report_date,
            primary_document=command.primary_document,
            source_url=command.source_url,
            content_hash=command.content_hash,
            source_metadata=command.source_metadata,
        )
        .on_conflict_do_nothing(
            index_elements=[Filing.accession_number],
        )
        .returning(Filing)
    )
    result = await session.execute(statement)
    filing = result.scalar_one_or_none()

    if filing is not None:
        return StoredFiling(filing=filing, created=True)

    existing = await get_filing_by_accession_number(
        session,
        command.accession_number,
    )

    if existing is None:
        raise FilingPersistenceError("filing insert conflicted but no existing record was found")

    _verify_existing_filing(existing, command)
    return StoredFiling(filing=existing, created=False)


async def store_filing_content(
    session: AsyncSession,
    filing_id: UUID,
    sections: Sequence[FilingSectionCreate],
) -> StoredFilingContent:
    """Persist parsed content for a newly created filing in the same transaction."""

    section_models: list[FilingSection] = []
    chunk_count = 0

    for section in sections:
        section_model = FilingSection(
            filing_id=filing_id,
            section_name=section.section_name,
            sequence_number=section.sequence_number,
            content=section.content,
            content_hash=section.content_hash,
            char_count=section.char_count,
            source_metadata=section.source_metadata,
        )
        section_model.chunks = [
            FilingChunk(
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                content_hash=chunk.content_hash,
                token_count=chunk.token_count,
                source_metadata=chunk.source_metadata,
            )
            for chunk in section.chunks
        ]
        chunk_count += len(section_model.chunks)
        section_models.append(section_model)

    session.add_all(section_models)
    await session.flush()
    return StoredFilingContent(
        section_count=len(section_models),
        chunk_count=chunk_count,
    )


async def store_financial_facts(
    session: AsyncSession,
    company_id: UUID,
    facts: Sequence[FinancialFactCreate],
) -> StoredFinancialFacts:
    """Bulk-insert unseen fact observations by deterministic source identity."""

    if not facts:
        return StoredFinancialFacts(created_count=0, existing_count=0)

    created_count = 0

    for batch_start in range(0, len(facts), MAX_FINANCIAL_FACTS_PER_INSERT):
        batch = facts[batch_start : batch_start + MAX_FINANCIAL_FACTS_PER_INSERT]
        statement = (
            insert(FinancialFact)
            .values(
                [
                    {
                        "company_id": company_id,
                        "observation_key": fact.observation_key,
                        "taxonomy": fact.taxonomy,
                        "concept": fact.concept,
                        "label": fact.label,
                        "description": fact.description,
                        "unit": fact.unit,
                        "value": fact.value,
                        "start_date": fact.start_date,
                        "end_date": fact.end_date,
                        "filed_date": fact.filed_date,
                        "fiscal_year": fact.fiscal_year,
                        "fiscal_period": fact.fiscal_period,
                        "form_type": fact.form_type,
                        "accession_number": fact.accession_number,
                        "frame": fact.frame,
                        "source_metadata": fact.source_metadata,
                    }
                    for fact in batch
                ]
            )
            .on_conflict_do_nothing(index_elements=[FinancialFact.observation_key])
            .returning(FinancialFact.id)
        )
        result = await session.execute(statement)
        created_count += len(result.scalars().all())

    return StoredFinancialFacts(
        created_count=created_count,
        existing_count=len(facts) - created_count,
    )


def _verify_existing_filing(
    existing: Filing,
    command: FilingCreate,
) -> None:
    """Reject reused accession numbers with inconsistent immutable fields."""

    mismatched: list[str] = []

    if existing.company_id != command.company_id:
        mismatched.append("company_id")
    if existing.form_type != command.form_type:
        mismatched.append("form_type")
    if existing.filing_date != command.filing_date:
        mismatched.append("filing_date")
    if existing.report_date != command.report_date:
        mismatched.append("report_date")
    if existing.primary_document != command.primary_document:
        mismatched.append("primary_document")
    if existing.source_url != command.source_url:
        mismatched.append("source_url")
    if existing.content_hash != command.content_hash:
        mismatched.append("content_hash")

    if mismatched:
        fields = ", ".join(mismatched)
        raise FilingIdentityConflictError(
            f"accession {command.accession_number} conflicts on: {fields}"
        )
