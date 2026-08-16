"""PostgreSQL integration test for grounded answers and exact fact validation."""

import os
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete

from finsight.agents.contracts import (
    FinancialFactEvidence,
    GeneratedCalculation,
    GeneratedClaim,
    GroundedAnswerDraft,
    InvestigationQuery,
)
from finsight.agents.investigation import answer_investigation
from finsight.config.settings import Settings
from finsight.retrieval.search import HybridSearchResult
from finsight.storage.database import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from finsight.storage.models import Company, Filing, FilingChunk, FilingSection, FinancialFact

RUN_DATABASE_TESTS = os.getenv("FINSIGHT_RUN_DATABASE_TESTS") == "1"
TEST_CIK = "0000000044"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason="set FINSIGHT_RUN_DATABASE_TESTS=1 to run database integration tests",
    ),
]


class QueryProvider:
    """Deterministic semantic query provider for real pgvector search."""

    model_name = "integration-grounding-model"
    dimensions = 1536

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Represent every question near the stored integration passage."""

        return [[1.0, *([0.0] * (self.dimensions - 1))] for _ in texts]


class DraftGenerator:
    """Deterministic structured-output generator for integration validation."""

    model_name = "integration-answer-model"

    async def generate(
        self,
        *,
        question: str,
        passages: Sequence[HybridSearchResult],
        facts: Sequence[FinancialFactEvidence],
    ) -> GroundedAnswerDraft:
        """Reference the request-local IDs assigned by the service."""

        assert question == "What supply risk and revenue are reported?"
        assert passages[0].content.startswith("Supply constraints")
        assert facts[0].concept == "Revenue"
        return GroundedAnswerDraft(
            claims=[
                GeneratedClaim(
                    statement="The filing identifies supply constraints as a risk.",
                    evidence_ids=["E1"],
                )
            ],
            calculations=[
                GeneratedCalculation(
                    statement="Reported revenue was 100 USD.",
                    operation="identity",
                    fact_ids=["F1"],
                    reported_value="100",
                    reported_unit="USD",
                )
            ],
        )


@pytest.mark.asyncio
async def test_grounded_answer_uses_real_retrieval_and_financial_facts() -> None:
    """One database path should retrieve, cite, and recompute exact SEC evidence."""

    engine = create_database_engine(Settings())
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == TEST_CIK))

        async with session_scope(factory) as setup_session:
            company = Company(
                cik=TEST_CIK,
                legal_name="Grounded Answer Test Company",
                ticker="GROUND",
            )
            filing = Filing(
                accession_number="0000000044-26-000001",
                form_type="10-K",
                filing_date=date(2026, 2, 1),
                report_date=date(2025, 12, 31),
                primary_document="grounded.htm",
                source_url="https://example.invalid/grounded.htm",
                content_hash="1" * 64,
            )
            section = FilingSection(
                section_name="Item 1A. Risk Factors",
                sequence_number=0,
                content="Supply constraints may materially affect operations.",
                content_hash="2" * 64,
                char_count=52,
            )
            section.chunks = [
                FilingChunk(
                    chunk_index=0,
                    content="Supply constraints may materially affect operations.",
                    content_hash="3" * 64,
                    token_count=7,
                    embedding=[1.0, *([0.0] * 1535)],
                    embedding_model=QueryProvider.model_name,
                )
            ]
            filing.sections = [section]
            company.filings = [filing]
            company.financial_facts = [
                FinancialFact(
                    observation_key="4" * 64,
                    taxonomy="us-gaap",
                    concept="Revenue",
                    label="Revenue",
                    description="Annual revenue.",
                    unit="USD",
                    value=Decimal("100"),
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 12, 31),
                    filed_date=date(2026, 2, 1),
                    fiscal_year=2025,
                    fiscal_period="FY",
                    form_type="10-K",
                    accession_number="0000000044-26-000001",
                    frame="CY2025",
                )
            ]
            setup_session.add(company)

        result = await answer_investigation(
            query=InvestigationQuery(
                question="What supply risk and revenue are reported?",
                cik=TEST_CIK,
                form_types=("10-K",),
                fact_concepts=("Revenue",),
                top_k=1,
                candidate_k=5,
            ),
            embedding_provider=QueryProvider(),
            answer_generator=DraftGenerator(),
            session_factory=factory,
        )

        assert result.status == "grounded"
        assert "[E1]" in result.answer
        assert "[F1]" in result.answer
        assert result.numerical_validations[0].passed is True
        assert [source.source_type for source in result.sources] == [
            "filing_passage",
            "financial_fact",
        ]
    finally:
        async with session_scope(factory) as cleanup_session:
            await cleanup_session.execute(delete(Company).where(Company.cik == TEST_CIK))
        await engine.dispose()
