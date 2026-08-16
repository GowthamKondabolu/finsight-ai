"""Tests for retrieval-to-answer orchestration and grounding guardrails."""

from datetime import date
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import finsight.agents.investigation as investigation_module
from finsight.agents.contracts import (
    GeneratedCalculation,
    GeneratedClaim,
    GroundedAnswerDraft,
    InvestigationQuery,
)
from finsight.agents.generation import AnswerGenerator
from finsight.agents.investigation import (
    GroundedAnswerContractError,
    answer_investigation,
)
from finsight.retrieval.embeddings import EmbeddingProvider
from finsight.retrieval.search import HybridSearchResult, RetrievalCitation
from finsight.storage.database import SessionFactory
from finsight.storage.fact_queries import FinancialFactRecord


def passage() -> HybridSearchResult:
    """Return one citation-complete retrieved passage."""

    return HybridSearchResult(
        chunk_id=UUID("00000000-0000-4000-8000-000000000001"),
        content="Supply constraints may affect operations.",
        content_hash="a" * 64,
        score=0.03,
        keyword_rank=1,
        semantic_rank=1,
        keyword_score=0.8,
        semantic_score=0.9,
        matched_by=("keyword", "semantic"),
        citation=RetrievalCitation(
            company_name="Apple Inc.",
            cik="0000320193",
            ticker="AAPL",
            accession_number="0000320193-25-000079",
            form_type="10-K",
            filing_date=date(2025, 10, 31),
            report_date=date(2025, 9, 27),
            section_name="Item 1A. Risk Factors",
            section_sequence=1,
            chunk_index=0,
            source_url="https://www.sec.gov/example",
        ),
        chunk_metadata={},
    )


def fact_record(
    key: str,
    concept: str,
    value: str,
    end_date: date,
) -> FinancialFactRecord:
    """Return one exact repository fact record."""

    return FinancialFactRecord(
        observation_key=key * 64,
        concept=concept,
        label=concept,
        unit="USD",
        value=Decimal(value),
        start_date=date(end_date.year, 1, 1),
        end_date=end_date,
        filed_date=date(end_date.year + 1, 2, 1),
        fiscal_year=end_date.year,
        fiscal_period="FY",
        form_type="10-K",
        accession_number=f"0000320193-{str(end_date.year + 1)[-2:]}-000001",
    )


def providers(draft: GroundedAnswerDraft) -> tuple[EmbeddingProvider, AnswerGenerator]:
    """Return typed deterministic provider doubles."""

    embedding = Mock()
    embedding.model_name = "embedding-model"
    embedding.dimensions = 2
    embedding.embed = AsyncMock(return_value=[[1.0, 0.0]])
    generator = Mock()
    generator.model_name = "generation-model"
    generator.generate = AsyncMock(return_value=draft)
    return cast(EmbeddingProvider, embedding), cast(AnswerGenerator, generator)


def session_factory() -> SessionFactory:
    """Return a read-session context double."""

    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    return cast(SessionFactory, Mock(return_value=session))


@pytest.mark.asyncio
async def test_investigation_renders_only_validated_cited_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Narrative and arithmetic should carry verified local source identifiers."""

    search = AsyncMock(return_value=[passage()])
    facts = [
        fact_record("1", "Revenue", "100", date(2025, 12, 31)),
        fact_record("2", "Revenue", "80", date(2024, 12, 31)),
    ]
    fact_query = AsyncMock(return_value=facts)
    monkeypatch.setattr(investigation_module, "hybrid_search", search)
    monkeypatch.setattr(investigation_module, "list_financial_facts", fact_query)
    draft = GroundedAnswerDraft(
        claims=[GeneratedClaim(statement="Supply constraints remain a risk.", evidence_ids=["E1"])],
        calculations=[
            GeneratedCalculation(
                statement="Revenue increased by 25 percent.",
                operation="percentage_change",
                fact_ids=["F2", "F1"],
                reported_value="25",
                reported_unit="%",
            )
        ],
        limitations=["Only filed historical information was reviewed."],
    )
    embedding, generator = providers(draft)

    result = await answer_investigation(
        query=InvestigationQuery(
            question="  What changed?  ",
            cik="320193",
            form_types=("10-K",),
            fact_concepts=("Revenue",),
        ),
        embedding_provider=embedding,
        answer_generator=generator,
        session_factory=session_factory(),
    )

    assert result.status == "grounded"
    assert result.question == "What changed?"
    assert "[E1]" in result.answer
    assert "[F2] [F1]" in result.answer
    assert result.numerical_validations[0].passed is True
    assert [source.source_id for source in result.sources] == ["E1", "F1", "F2"]
    assert result.sources[0].content_hash == "a" * 64
    assert result.sources[1].fact_value == "100"
    assert result.requires_human_review is True
    assert result.review_reasons == ("financial analysis requires qualified human review",)
    fact_query.assert_awaited_once()
    assert fact_query.await_args is not None
    assert fact_query.await_args.kwargs["cik"] == "0000320193"
    assert fact_query.await_args.kwargs["concepts"] == ("Revenue",)
    cast(AsyncMock, generator.generate).assert_awaited_once()


@pytest.mark.asyncio
async def test_investigation_stops_before_generation_when_retrieval_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No evidence should produce a fixed safe response without an LLM call."""

    monkeypatch.setattr(investigation_module, "hybrid_search", AsyncMock(return_value=[]))
    embedding, generator = providers(GroundedAnswerDraft())

    result = await answer_investigation(
        query=InvestigationQuery(question="risk?"),
        embedding_provider=embedding,
        answer_generator=generator,
        session_factory=session_factory(),
    )

    assert result.status == "insufficient_evidence"
    assert result.sources == ()
    assert result.model_name is None
    assert "No filing passages" in result.limitations[0]
    assert "insufficient retrieved evidence" in result.review_reasons
    cast(AsyncMock, generator.generate).assert_not_awaited()


@pytest.mark.asyncio
async def test_investigation_rejects_unknown_generated_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invented model citation identifiers must fail the output contract."""

    monkeypatch.setattr(investigation_module, "hybrid_search", AsyncMock(return_value=[passage()]))
    draft = GroundedAnswerDraft(
        claims=[GeneratedClaim(statement="Unsupported.", evidence_ids=["E99"])]
    )
    embedding, generator = providers(draft)

    with pytest.raises(GroundedAnswerContractError, match="E99"):
        await answer_investigation(
            query=InvestigationQuery(question="risk?", fact_limit=0),
            embedding_provider=embedding,
            answer_generator=generator,
            session_factory=session_factory(),
        )


@pytest.mark.asyncio
async def test_investigation_excludes_failed_calculations_and_requires_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mismatched model arithmetic should never appear in the rendered answer."""

    monkeypatch.setattr(investigation_module, "hybrid_search", AsyncMock(return_value=[passage()]))
    monkeypatch.setattr(
        investigation_module,
        "list_financial_facts",
        AsyncMock(return_value=[fact_record("1", "Revenue", "100", date(2025, 12, 31))]),
    )
    draft = GroundedAnswerDraft(
        calculations=[
            GeneratedCalculation(
                statement="Revenue was 999 dollars.",
                operation="identity",
                fact_ids=["F1"],
                reported_value="999",
                reported_unit="USD",
            )
        ]
    )
    embedding, generator = providers(draft)

    result = await answer_investigation(
        query=InvestigationQuery(question="revenue?", cik="320193"),
        embedding_provider=embedding,
        answer_generator=generator,
        session_factory=session_factory(),
    )

    assert result.status == "needs_review"
    assert "999" not in result.answer
    assert result.numerical_validations[0].passed is False
    assert "failed deterministic validation" in result.review_reasons[1]
    assert result.limitations == ("reported arithmetic or unit does not match exact SEC facts",)


@pytest.mark.asyncio
async def test_investigation_reports_empty_supported_draft_as_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schema-valid but empty draft must not be presented as a grounded answer."""

    monkeypatch.setattr(investigation_module, "hybrid_search", AsyncMock(return_value=[passage()]))
    embedding, generator = providers(
        GroundedAnswerDraft(limitations=["The evidence did not answer the question."])
    )

    result = await answer_investigation(
        query=InvestigationQuery(question="risk?", fact_limit=0),
        embedding_provider=embedding,
        answer_generator=generator,
        session_factory=session_factory(),
    )

    assert result.status == "insufficient_evidence"
    assert "insufficient" in result.answer
    assert "generation produced no supported claims" in result.review_reasons


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "message"),
    [
        (InvestigationQuery(question="   "), "question"),
        (
            InvestigationQuery(
                question="risk",
                cik="320193",
                fact_concepts=("Revenue", "Revenue"),
            ),
            "fact_concepts",
        ),
        (
            InvestigationQuery(question="risk", cik="320193", fact_concepts=("",)),
            "fact_concepts",
        ),
    ],
)
async def test_investigation_rejects_invalid_service_inputs(
    query: InvestigationQuery,
    message: str,
) -> None:
    """Whitespace and ambiguous concepts should fail before provider work."""

    embedding, generator = providers(GroundedAnswerDraft())
    with pytest.raises(ValueError, match=message):
        await answer_investigation(
            query=query,
            embedding_provider=embedding,
            answer_generator=generator,
            session_factory=session_factory(),
        )


@pytest.mark.asyncio
async def test_investigation_without_cik_skips_financial_fact_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-company narrative questions must not receive ambiguous fact evidence."""

    monkeypatch.setattr(investigation_module, "hybrid_search", AsyncMock(return_value=[passage()]))
    fact_query = AsyncMock()
    monkeypatch.setattr(investigation_module, "list_financial_facts", fact_query)
    embedding, generator = providers(
        GroundedAnswerDraft(claims=[GeneratedClaim(statement="Risk exists.", evidence_ids=["E1"])])
    )

    result = await answer_investigation(
        query=InvestigationQuery(question="risk?"),
        embedding_provider=embedding,
        answer_generator=generator,
        session_factory=session_factory(),
    )

    assert result.status == "grounded"
    assert [source.source_id for source in result.sources] == ["E1"]
    fact_query.assert_not_awaited()
