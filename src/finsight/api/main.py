"""FinSight AI FastAPI application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, status

from finsight import __version__
from finsight.api.schemas import (
    HealthResponse,
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


def create_app(
    settings: Settings | None = None,
    retrieval_handler: RetrievalHandler | None = None,
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

    return application


app = create_app()
