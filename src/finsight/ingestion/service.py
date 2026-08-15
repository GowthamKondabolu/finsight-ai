"""Transaction-aware orchestration for SEC company and filing ingestion."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from finsight.ingestion.sec_client import SecEdgarClient, SecFilingDocument
from finsight.ingestion.sec_schemas import SecFilingMetadata
from finsight.storage.database import SessionFactory, session_scope
from finsight.storage.repositories import (
    CompanyUpsert,
    FilingCreate,
    find_existing_accession_numbers,
    store_filing,
    upsert_company,
)

DEFAULT_FILING_FORMS = frozenset({"10-K", "10-Q", "8-K"})
MAX_FILINGS_PER_RUN = 100


@dataclass(frozen=True, slots=True)
class SecIngestionResult:
    """Observable result of one company ingestion operation."""

    cik: str
    company_id: UUID
    discovered_filings: int
    selected_filings: int
    downloaded_filings: int
    created_filings: int
    skipped_existing_filings: int
    selected_forms: tuple[str, ...]


async def ingest_company_filings(
    *,
    client: SecEdgarClient,
    session_factory: SessionFactory,
    cik: str | int,
    forms: Collection[str] = DEFAULT_FILING_FORMS,
    limit: int = 10,
) -> SecIngestionResult:
    """Ingest selected recent SEC filings without long database transactions."""

    selected_forms = frozenset(form.strip().upper() for form in forms if form.strip())

    if not selected_forms:
        raise ValueError("at least one SEC filing form must be selected")

    if not 1 <= limit <= MAX_FILINGS_PER_RUN:
        raise ValueError(f"limit must be between 1 and {MAX_FILINGS_PER_RUN}")

    submissions = await client.fetch_company_submissions(cik)
    discovered = submissions.filings.recent.to_records()
    selected = [filing for filing in discovered if filing.form_type.upper() in selected_forms][
        :limit
    ]

    existing_accessions: set[str] = set()

    if selected:
        async with session_factory() as read_session:
            existing_accessions = await find_existing_accession_numbers(
                read_session,
                [filing.accession_number for filing in selected],
            )

    downloaded: list[tuple[SecFilingMetadata, SecFilingDocument]] = []

    for filing in selected:
        if filing.accession_number in existing_accessions:
            continue

        document = await client.fetch_filing_document(
            submissions.cik,
            filing,
        )
        downloaded.append((filing, document))

    created_filings = 0

    async with session_scope(session_factory) as write_session:
        company = await upsert_company(
            write_session,
            CompanyUpsert(
                cik=submissions.cik,
                legal_name=submissions.name,
                ticker=submissions.primary_ticker,
                sic=submissions.sic,
                fiscal_year_end=submissions.fiscal_year_end,
            ),
        )

        for filing, document in downloaded:
            stored = await store_filing(
                write_session,
                FilingCreate(
                    company_id=company.id,
                    accession_number=filing.accession_number,
                    form_type=filing.form_type,
                    filing_date=filing.filing_date,
                    report_date=filing.report_date,
                    primary_document=filing.primary_document,
                    source_url=document.source_url,
                    content_hash=document.content_hash,
                    source_metadata={
                        "provider": "sec-edgar",
                        "content_type": document.content_type,
                        "content_length": len(document.content),
                    },
                ),
            )
            created_filings += int(stored.created)

        company_id = company.id

    return SecIngestionResult(
        cik=submissions.cik,
        company_id=company_id,
        discovered_filings=len(discovered),
        selected_filings=len(selected),
        downloaded_filings=len(downloaded),
        created_filings=created_filings,
        skipped_existing_filings=len(selected) - created_filings,
        selected_forms=tuple(sorted(selected_forms)),
    )
