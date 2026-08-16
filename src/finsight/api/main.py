"""FinSight AI FastAPI application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, status

from finsight import __version__
from finsight.agents.contracts import GroundedAnswerResult, InvestigationQuery
from finsight.agents.generation import AnswerGenerationError, OpenAIAnswerGenerator
from finsight.agents.investigation import GroundedAnswerContractError, answer_investigation
from finsight.api.schemas import (
    HealthResponse,
    InvestigationAnswerRequest,
    InvestigationAnswerResponse,
    RetrievalResultResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from finsight.config.settings import Settings, get_settings
from finsight.retrieval.embeddings import OpenAIEmbeddingProvider
from finsight.retrieval.search import (
    HybridSearchResult,
    RetrievalQuery,
    hybrid_search,
)
from finsight.storage.database import create_database_engine, create_session_factory

RetrievalHandler = Callable[[RetrievalQuery], Awaitable[list[HybridSearchResult]]]
InvestigationHandler = Callable[[InvestigationQuery], Awaitable[GroundedAnswerResult]]


async def run_retrieval_query(
    *,
    settings: Settings,
    query: RetrievalQuery,
) -> list[HybridSearchResult]:
    """Run production retrieval and release provider and database resources."""

    provider = OpenAIEmbeddingProvider.from_settings(settings)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with provider:
            return await hybrid_search(
                query=query,
                provider=provider,
                session_factory=session_factory,
            )
    finally:
        await engine.dispose()


async def run_investigation_query(
    *,
    settings: Settings,
    query: InvestigationQuery,
) -> GroundedAnswerResult:
    """Run production investigation and release provider and database resources."""

    embedding_provider = OpenAIEmbeddingProvider.from_settings(settings)
    answer_generator = OpenAIAnswerGenerator.from_settings(settings)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with embedding_provider, answer_generator:
            return await answer_investigation(
                query=query,
                embedding_provider=embedding_provider,
                answer_generator=answer_generator,
                session_factory=session_factory,
            )
    finally:
        await engine.dispose()


def create_app(
    settings: Settings | None = None,
    retrieval_handler: RetrievalHandler | None = None,
    investigation_handler: InvestigationHandler | None = None,
) -> FastAPI:
    """Create an application using explicit or environment-based settings."""

    resolved_settings = settings or get_settings()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        description="Agentic financial risk intelligence over public SEC filings.",
    )

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["operations"],
    )
    async def health() -> HealthResponse:
        """Return service identity and runtime environment."""

        return HealthResponse(
            service=resolved_settings.app_name,
            version=__version__,
            environment=resolved_settings.environment,
        )

    @application.post(
        "/v1/retrieval/search",
        response_model=RetrievalSearchResponse,
        tags=["retrieval"],
    )
    async def search(request: RetrievalSearchRequest) -> RetrievalSearchResponse:
        """Return citation-complete hybrid retrieval results."""

        query = RetrievalQuery(
            text=request.query,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            cik=request.cik,
            form_types=tuple(request.form_types),
            filed_from=request.filed_from,
            filed_to=request.filed_to,
            section_names=tuple(request.section_names),
        )
        if retrieval_handler is None:
            try:
                results = await run_retrieval_query(
                    settings=resolved_settings,
                    query=query,
                )
            except ValueError as exc:
                if "FINSIGHT_OPENAI_API_KEY" not in str(exc):
                    raise
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="retrieval embedding provider is not configured",
                ) from exc
        else:
            results = await retrieval_handler(query)

        response_results = [
            RetrievalResultResponse.model_validate(asdict(result)) for result in results
        ]
        return RetrievalSearchResponse(
            query=query.text,
            count=len(response_results),
            results=response_results,
        )

    @application.post(
        "/v1/investigations/answer",
        response_model=InvestigationAnswerResponse,
        tags=["investigations"],
    )
    async def answer(request: InvestigationAnswerRequest) -> InvestigationAnswerResponse:
        """Return citation-enforced claims and deterministic numerical checks."""

        query = InvestigationQuery(
            question=request.question,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            cik=request.cik,
            form_types=tuple(request.form_types),
            filed_from=request.filed_from,
            filed_to=request.filed_to,
            section_names=tuple(request.section_names),
            fact_concepts=tuple(request.fact_concepts),
            fact_limit=request.fact_limit,
        )
        if investigation_handler is not None:
            result = await investigation_handler(query)
        else:
            try:
                result = await run_investigation_query(
                    settings=resolved_settings,
                    query=query,
                )
            except ValueError as exc:
                if "FINSIGHT_OPENAI_API_KEY" not in str(exc):
                    raise
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="investigation AI providers are not configured",
                ) from exc
            except (AnswerGenerationError, GroundedAnswerContractError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="generated answer failed the grounding contract",
                ) from exc

        return InvestigationAnswerResponse.model_validate(asdict(result))

    return application


app = create_app()
