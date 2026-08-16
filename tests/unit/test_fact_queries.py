"""Tests for read-only financial-fact investigation queries."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from finsight.storage.fact_queries import list_financial_facts


def fact_row() -> SimpleNamespace:
    """Return one complete financial-fact result row."""

    return SimpleNamespace(
        _mapping={
            "observation_key": "a" * 64,
            "concept": "Revenue",
            "label": "Revenue",
            "unit": "USD",
            "value": Decimal("100"),
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 12, 31),
            "filed_date": date(2026, 2, 1),
            "fiscal_year": 2025,
            "fiscal_period": "FY",
            "form_type": "10-K",
            "accession_number": "0000000001-26-000001",
        }
    )


@pytest.mark.asyncio
async def test_fact_query_applies_issuer_concepts_order_and_limit() -> None:
    """Fact evidence should be exact, bounded, and deterministically ordered."""

    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.all.return_value = [fact_row()]
    session.execute.return_value = result

    records = await list_financial_facts(
        session,
        cik="0000320193",
        concepts=("Revenue", "Assets"),
        limit=20,
    )

    assert records[0].value == Decimal("100")
    assert records[0].concept == "Revenue"
    statement = str(session.execute.await_args.args[0])
    assert "companies.cik" in statement
    assert "financial_facts.concept IN" in statement
    assert "financial_facts.filed_date DESC" in statement
    assert "LIMIT" in statement


@pytest.mark.asyncio
async def test_fact_query_allows_bounded_unfiltered_concepts() -> None:
    """An empty concept selection should return a latest-first issuer sample."""

    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.all.return_value = []
    session.execute.return_value = result

    assert (
        await list_financial_facts(
            session,
            cik="0000320193",
            concepts=(),
            limit=1,
        )
        == []
    )
    assert "financial_facts.concept IN" not in str(session.execute.await_args.args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 101])
async def test_fact_query_rejects_unbounded_limits(limit: int) -> None:
    """Fact context must remain bounded before SQL execution."""

    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="between 1 and 100"):
        await list_financial_facts(session, cik="0000320193", concepts=(), limit=limit)
    session.execute.assert_not_awaited()
