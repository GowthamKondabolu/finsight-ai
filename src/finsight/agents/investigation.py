"""Retrieval-to-answer orchestration with citation and numerical guardrails."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from finsight.agents.contracts import (
    AnswerSource,
    FinancialFactEvidence,
    GroundedAnswerResult,
    InvestigationQuery,
    NumericalValidation,
    ValidatedClaim,
)
from finsight.agents.generation import AnswerGenerator
from finsight.guardrails.numerical import validate_calculation
from finsight.ingestion.sec_schemas import normalize_cik
from finsight.retrieval.embeddings import EmbeddingProvider
from finsight.retrieval.search import RetrievalQuery, hybrid_search
from finsight.storage.database import SessionFactory
from finsight.storage.fact_queries import list_financial_facts

HUMAN_REVIEW_REASON = "financial analysis requires qualified human review"


class GroundedAnswerContractError(RuntimeError):
    """Raised when generated claims cite evidence outside the supplied context."""


def _normalize_concepts(values: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize exact XBRL concepts and reject blanks or duplicates."""

    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError("fact_concepts cannot contain blank or duplicate values")
    return tuple(sorted(normalized))


async def _load_fact_evidence(
    *,
    session_factory: SessionFactory,
    cik: str | None,
    concepts: tuple[str, ...],
    limit: int,
) -> list[FinancialFactEvidence]:
    """Load issuer-specific facts and assign request-local citation identifiers."""

    if cik is None or limit == 0:
        return []
    normalized_cik = normalize_cik(cik)
    async with session_factory() as session:
        records = await list_financial_facts(
            session,
            cik=normalized_cik,
            concepts=concepts,
            limit=limit,
        )
    source_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{normalized_cik}.json"
    return [
        FinancialFactEvidence(
            source_id=f"F{index}",
            observation_key=record.observation_key,
            concept=record.concept,
            label=record.label,
            unit=record.unit,
            value=record.value,
            start_date=record.start_date,
            end_date=record.end_date,
            filed_date=record.filed_date,
            fiscal_year=record.fiscal_year,
            fiscal_period=record.fiscal_period,
            form_type=record.form_type,
            accession_number=record.accession_number,
            source_url=source_url,
        )
        for index, record in enumerate(records, start=1)
    ]


def _render_answer(
    claims: Sequence[ValidatedClaim],
    validations: Sequence[NumericalValidation],
) -> str:
    """Render only citation-checked claims and numerically valid calculations."""

    sentences = [
        f"{claim.statement} {' '.join(f'[{source_id}]' for source_id in claim.citation_ids)}"
        for claim in claims
    ]
    sentences.extend(
        f"{validation.statement} {' '.join(f'[{source_id}]' for source_id in validation.fact_ids)}"
        for validation in validations
        if validation.passed
    )
    if not sentences:
        return "The available SEC evidence is insufficient to produce a grounded answer."
    return "\n\n".join(sentences)


async def answer_investigation(
    *,
    query: InvestigationQuery,
    embedding_provider: EmbeddingProvider,
    answer_generator: AnswerGenerator,
    session_factory: SessionFactory,
) -> GroundedAnswerResult:
    """Retrieve evidence, generate structured claims, and enforce guardrails."""

    question = query.question.strip()
    if not question:
        raise ValueError("investigation question cannot be blank")
    concepts = _normalize_concepts(query.fact_concepts)
    retrieval_results = await hybrid_search(
        query=RetrievalQuery(
            text=question,
            top_k=query.top_k,
            candidate_k=query.candidate_k,
            cik=query.cik,
            form_types=query.form_types,
            filed_from=query.filed_from,
            filed_to=query.filed_to,
            section_names=query.section_names,
        ),
        provider=embedding_provider,
        session_factory=session_factory,
    )
    if not retrieval_results:
        return GroundedAnswerResult(
            question=question,
            status="insufficient_evidence",
            answer="The available SEC evidence is insufficient to produce a grounded answer.",
            claims=(),
            numerical_validations=(),
            sources=(),
            limitations=("No filing passages matched the bounded retrieval request.",),
            model_name=None,
            requires_human_review=True,
            review_reasons=(HUMAN_REVIEW_REASON, "insufficient retrieved evidence"),
        )

    facts = await _load_fact_evidence(
        session_factory=session_factory,
        cik=query.cik,
        concepts=concepts,
        limit=query.fact_limit,
    )
    draft = await answer_generator.generate(
        question=question,
        passages=retrieval_results,
        facts=facts,
    )

    passage_sources = [
        AnswerSource(
            source_id=f"E{index}",
            source_type="filing_passage",
            label=f"{result.citation.form_type} — {result.citation.section_name}",
            source_url=result.citation.source_url,
            accession_number=result.citation.accession_number,
            form_type=result.citation.form_type,
            filing_date=result.citation.filing_date,
            section_name=result.citation.section_name,
            chunk_index=result.citation.chunk_index,
            content_hash=result.content_hash,
        )
        for index, result in enumerate(retrieval_results, start=1)
    ]
    fact_sources = [
        AnswerSource(
            source_id=fact.source_id,
            source_type="financial_fact",
            label=fact.label,
            source_url=fact.source_url,
            accession_number=fact.accession_number,
            form_type=fact.form_type,
            fact_concept=fact.concept,
            fact_value=format(fact.value, "f"),
            fact_unit=fact.unit,
            fact_end_date=fact.end_date,
        )
        for fact in facts
    ]
    sources = passage_sources + fact_sources
    known_ids = {source.source_id for source in sources}

    claims: list[ValidatedClaim] = []
    for claim in draft.claims:
        unknown = [source_id for source_id in claim.evidence_ids if source_id not in known_ids]
        if unknown:
            raise GroundedAnswerContractError(
                f"generated claim cited unknown evidence: {', '.join(unknown)}"
            )
        claims.append(
            ValidatedClaim(
                statement=claim.statement,
                citation_ids=tuple(claim.evidence_ids),
            )
        )

    fact_map = {fact.source_id: fact for fact in facts}
    validations = [
        validate_calculation(calculation, fact_map) for calculation in draft.calculations
    ]
    failed_validations = [validation for validation in validations if not validation.passed]

    review_reasons = [HUMAN_REVIEW_REASON]
    if failed_validations:
        review_reasons.append("one or more generated calculations failed deterministic validation")
    if not claims and not any(validation.passed for validation in validations):
        review_reasons.append("generation produced no supported claims")

    if failed_validations:
        status: Literal["grounded", "insufficient_evidence", "needs_review"] = "needs_review"
    elif not claims and not any(validation.passed for validation in validations):
        status = "insufficient_evidence"
    else:
        status = "grounded"

    limitations = list(draft.limitations)
    limitations.extend(validation.message for validation in failed_validations)
    return GroundedAnswerResult(
        question=question,
        status=status,
        answer=_render_answer(claims, validations),
        claims=tuple(claims),
        numerical_validations=tuple(validations),
        sources=tuple(sources),
        limitations=tuple(dict.fromkeys(limitations)),
        model_name=answer_generator.model_name,
        requires_human_review=True,
        review_reasons=tuple(review_reasons),
    )
