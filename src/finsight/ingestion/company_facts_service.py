"""Transaction-aware orchestration for SEC company-facts ingestion."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from finsight.ingestion.sec_client import SecEdgarClient
from finsight.storage.database import SessionFactory, session_scope
from finsight.storage.repositories import (
    CompanyUpsert,
    FinancialFactCreate,
    store_financial_facts,
    upsert_company,
)

DEFAULT_COMPANY_FACT_TAXONOMIES = frozenset({"dei", "us-gaap"})


@dataclass(frozen=True, slots=True)
class CompanyFactsIngestionResult:
    """Observable result of one issuer company-facts ingestion."""

    cik: str
    company_id: UUID
    discovered_observations: int
    selected_observations: int
    created_observations: int
    skipped_existing_observations: int
    selected_taxonomies: tuple[str, ...]


async def ingest_company_facts(
    *,
    client: SecEdgarClient,
    session_factory: SessionFactory,
    cik: str | int,
    taxonomies: Collection[str] = DEFAULT_COMPANY_FACT_TAXONOMIES,
) -> CompanyFactsIngestionResult:
    """Fetch, normalize, and idempotently persist selected SEC company facts."""

    selected_taxonomies = frozenset(
        taxonomy.strip().lower() for taxonomy in taxonomies if taxonomy.strip()
    )

    if not selected_taxonomies:
        raise ValueError("at least one SEC company-facts taxonomy must be selected")

    submissions = await client.fetch_company_submissions(cik)
    company_facts = await client.fetch_company_facts(submissions.cik)
    records = company_facts.to_records(selected_taxonomies)

    async with session_scope(session_factory) as session:
        company = await upsert_company(
            session,
            CompanyUpsert(
                cik=submissions.cik,
                legal_name=submissions.name,
                ticker=submissions.primary_ticker,
                sic=submissions.sic,
                fiscal_year_end=submissions.fiscal_year_end,
            ),
        )
        stored = await store_financial_facts(
            session,
            company.id,
            tuple(
                FinancialFactCreate(
                    observation_key=record.observation_key,
                    taxonomy=record.taxonomy,
                    concept=record.concept,
                    label=record.label,
                    description=record.description,
                    unit=record.unit,
                    value=record.value,
                    start_date=record.start_date,
                    end_date=record.end_date,
                    filed_date=record.filed_date,
                    fiscal_year=record.fiscal_year,
                    fiscal_period=record.fiscal_period,
                    form_type=record.form_type,
                    accession_number=record.accession_number,
                    frame=record.frame,
                    source_metadata={
                        "provider": "sec-companyfacts",
                        "entity_name": company_facts.entity_name,
                    },
                )
                for record in records
            ),
        )
        company_id = company.id

    return CompanyFactsIngestionResult(
        cik=submissions.cik,
        company_id=company_id,
        discovered_observations=company_facts.observation_count,
        selected_observations=len(records),
        created_observations=stored.created_count,
        skipped_existing_observations=stored.existing_count,
        selected_taxonomies=tuple(sorted(selected_taxonomies)),
    )
