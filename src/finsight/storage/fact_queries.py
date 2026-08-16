"""Read-only financial-fact queries for grounded investigations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finsight.storage.models import Company, FinancialFact


@dataclass(frozen=True, slots=True)
class FinancialFactRecord:
    """Exact normalized SEC fact returned to the investigation layer."""

    observation_key: str
    concept: str
    label: str
    unit: str
    value: Decimal
    start_date: date | None
    end_date: date
    filed_date: date
    fiscal_year: int | None
    fiscal_period: str | None
    form_type: str
    accession_number: str


async def list_financial_facts(
    session: AsyncSession,
    *,
    cik: str,
    concepts: tuple[str, ...],
    limit: int,
) -> list[FinancialFactRecord]:
    """Return a deterministic latest-first set of exact SEC observations."""

    if not 1 <= limit <= 100:
        raise ValueError("financial fact limit must be between 1 and 100")

    statement = (
        select(
            FinancialFact.observation_key,
            FinancialFact.concept,
            FinancialFact.label,
            FinancialFact.unit,
            FinancialFact.value,
            FinancialFact.start_date,
            FinancialFact.end_date,
            FinancialFact.filed_date,
            FinancialFact.fiscal_year,
            FinancialFact.fiscal_period,
            FinancialFact.form_type,
            FinancialFact.accession_number,
        )
        .join(Company, FinancialFact.company_id == Company.id)
        .where(Company.cik == cik)
    )
    if concepts:
        statement = statement.where(FinancialFact.concept.in_(concepts))
    statement = statement.order_by(
        FinancialFact.filed_date.desc(),
        FinancialFact.end_date.desc(),
        FinancialFact.concept,
        FinancialFact.observation_key,
    )
    result = await session.execute(statement.limit(limit))
    return [FinancialFactRecord(**dict(row._mapping)) for row in result.all()]
