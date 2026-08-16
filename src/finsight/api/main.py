"""FinSight AI FastAPI application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from uuid import UUID

from fastapi import FastAPI, HTTPException, status

from finsight import __version__
from finsight.agents.contracts import GroundedAnswerResult, InvestigationQuery
from finsight.agents.feedback import (
    InvestigationFeedbackConflictError,
    InvestigationFeedbackInput,
    InvestigationFeedbackResult,
    record_investigation_feedback,
)
from finsight.agents.generation import AnswerGenerationError, OpenAIAnswerGenerator
from finsight.agents.investigation import GroundedAnswerContractError, answer_investigation
from finsight.agents.workflow import (
    HumanReviewDecision,
    InvestigationWorkflowResult,
    WorkflowNotFoundError,
    WorkflowStateConflictError,
    postgres_investigation_workflow,
)
from finsight.api.schemas import (
    ExperimentAssignmentRequest,
    ExperimentAssignmentResponse,
    ExperimentEventRequest,
    ExperimentEventResponse,
    HealthResponse,
    HumanReviewDecisionRequest,
    HumanReviewDecisionResponse,
    HumanReviewRequestResponse,
    InvestigationAnswerRequest,
    InvestigationAnswerResponse,
    InvestigationFeedbackRequest,
    InvestigationFeedbackResponse,
    InvestigationWorkflowResponse,
    InvestigationWorkflowStartRequest,
    RetrievalResultResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from finsight.config.settings import Settings, get_settings
from finsight.experiments.contracts import (
    AssignmentResult,
    ExperimentAnalysisReport,
    ExperimentContractError,
    ExperimentEventInput,
    ExperimentEventResult,
    ExperimentNotFoundError,
)
from finsight.experiments.repositories import (
    analyze_registered_experiment,
    assign_experiment_variant,
    record_experiment_event,
)
from finsight.retrieval.embeddings import OpenAIEmbeddingProvider
from finsight.retrieval.search import (
    HybridSearchResult,
    RetrievalQuery,
    hybrid_search,
)
from finsight.storage.database import (
    create_database_engine,
    create_session_factory,
    session_scope,
)

RetrievalHandler = Callable[[RetrievalQuery], Awaitable[list[HybridSearchResult]]]
InvestigationHandler = Callable[[InvestigationQuery], Awaitable[GroundedAnswerResult]]
WorkflowStartHandler = Callable[[UUID, InvestigationQuery], Awaitable[InvestigationWorkflowResult]]
WorkflowReviewHandler = Callable[
    [UUID, HumanReviewDecision], Awaitable[InvestigationWorkflowResult]
]
WorkflowGetHandler = Callable[[UUID], Awaitable[InvestigationWorkflowResult]]
FeedbackHandler = Callable[
    [UUID, InvestigationFeedbackInput], Awaitable[InvestigationFeedbackResult]
]
ExperimentAssignmentHandler = Callable[[str, str], Awaitable[AssignmentResult]]
ExperimentEventHandler = Callable[[str, ExperimentEventInput], Awaitable[ExperimentEventResult]]
ExperimentAnalysisHandler = Callable[[str], Awaitable[ExperimentAnalysisReport]]


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


async def run_workflow_start(
    *,
    settings: Settings,
    thread_id: UUID,
    query: InvestigationQuery,
) -> InvestigationWorkflowResult:
    """Generate an answer, checkpoint it, and stop at the review interrupt."""

    embedding_provider = OpenAIEmbeddingProvider.from_settings(settings)
    answer_generator = OpenAIAnswerGenerator.from_settings(settings)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

    async def execute(request: InvestigationQuery) -> GroundedAnswerResult:
        return await answer_investigation(
            query=request,
            embedding_provider=embedding_provider,
            answer_generator=answer_generator,
            session_factory=session_factory,
        )

    try:
        async with (
            embedding_provider,
            answer_generator,
            postgres_investigation_workflow(
                settings=settings,
                executor=execute,
            ) as workflow,
        ):
            return await workflow.start(thread_id=thread_id, query=query)
    finally:
        await engine.dispose()


async def run_workflow_review(
    *,
    settings: Settings,
    thread_id: UUID,
    decision: HumanReviewDecision,
) -> InvestigationWorkflowResult:
    """Resume a persisted workflow without allocating AI provider resources."""

    async def unexpected_execution(_: InvestigationQuery) -> GroundedAnswerResult:
        raise WorkflowStateConflictError("review resume attempted to restart investigation")

    async with postgres_investigation_workflow(
        settings=settings,
        executor=unexpected_execution,
    ) as workflow:
        return await workflow.resume(thread_id=thread_id, decision=decision)


async def run_workflow_get(
    *,
    settings: Settings,
    thread_id: UUID,
) -> InvestigationWorkflowResult:
    """Read one persisted workflow without allocating AI provider resources."""

    async def unexpected_execution(_: InvestigationQuery) -> GroundedAnswerResult:
        raise WorkflowStateConflictError("workflow lookup attempted to restart investigation")

    async with postgres_investigation_workflow(
        settings=settings,
        executor=unexpected_execution,
    ) as workflow:
        return await workflow.get(thread_id=thread_id)


async def run_feedback(
    *,
    settings: Settings,
    thread_id: UUID,
    feedback: InvestigationFeedbackInput,
) -> InvestigationFeedbackResult:
    """Persist analyst feedback after confirming the review is terminal."""

    workflow = await run_workflow_get(settings=settings, thread_id=thread_id)
    if workflow.status == "pending_review":
        raise WorkflowStateConflictError("feedback requires a completed human review")

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            return await record_investigation_feedback(
                session,
                thread_id=thread_id,
                feedback=feedback,
            )
    finally:
        await engine.dispose()


async def run_experiment_assignment(
    *,
    settings: Settings,
    experiment_key: str,
    unit_id: str,
) -> AssignmentResult:
    """Persist one deterministic assignment and release database resources."""

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            return await assign_experiment_variant(
                session,
                experiment_key=experiment_key,
                unit_id=unit_id,
                assignment_secret=settings.experiment_assignment_secret.get_secret_value(),
            )
    finally:
        await engine.dispose()


async def run_experiment_event(
    *,
    settings: Settings,
    experiment_key: str,
    event_input: ExperimentEventInput,
) -> ExperimentEventResult:
    """Persist validated experiment telemetry and release database resources."""

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            return await record_experiment_event(
                session,
                experiment_key=experiment_key,
                event_input=event_input,
            )
    finally:
        await engine.dispose()


async def run_experiment_analysis(
    *,
    settings: Settings,
    experiment_key: str,
) -> ExperimentAnalysisReport:
    """Analyze registered experiment telemetry and release database resources."""

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            return await analyze_registered_experiment(
                session,
                experiment_key=experiment_key,
            )
    finally:
        await engine.dispose()


def _investigation_query(request: InvestigationAnswerRequest) -> InvestigationQuery:
    """Map a validated API request to the provider-independent domain contract."""

    return InvestigationQuery(
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


def _workflow_response(result: InvestigationWorkflowResult) -> InvestigationWorkflowResponse:
    """Translate durable domain state into the public API schema."""

    review_request = (
        HumanReviewRequestResponse.model_validate(result.review_request.model_dump())
        if result.review_request is not None
        else None
    )
    review_decision = (
        HumanReviewDecisionResponse.model_validate(result.review_decision.model_dump())
        if result.review_decision is not None
        else None
    )
    return InvestigationWorkflowResponse(
        thread_id=result.thread_id,
        status=result.status,
        release_authorized=result.status == "approved",
        answer=InvestigationAnswerResponse.model_validate(asdict(result.answer)),
        review_request=review_request,
        review_decision=review_decision,
    )


def create_app(
    settings: Settings | None = None,
    retrieval_handler: RetrievalHandler | None = None,
    investigation_handler: InvestigationHandler | None = None,
    workflow_start_handler: WorkflowStartHandler | None = None,
    workflow_review_handler: WorkflowReviewHandler | None = None,
    workflow_get_handler: WorkflowGetHandler | None = None,
    feedback_handler: FeedbackHandler | None = None,
    experiment_assignment_handler: ExperimentAssignmentHandler | None = None,
    experiment_event_handler: ExperimentEventHandler | None = None,
    experiment_analysis_handler: ExperimentAnalysisHandler | None = None,
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

        query = _investigation_query(request)
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

    @application.post(
        "/v1/investigations/runs",
        response_model=InvestigationWorkflowResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["investigations"],
    )
    async def start_workflow(
        request: InvestigationWorkflowStartRequest,
    ) -> InvestigationWorkflowResponse:
        """Start a durable investigation and pause before answer release."""

        query = _investigation_query(request)
        try:
            if workflow_start_handler is not None:
                result = await workflow_start_handler(request.thread_id, query)
            else:
                result = await run_workflow_start(
                    settings=resolved_settings,
                    thread_id=request.thread_id,
                    query=query,
                )
        except WorkflowStateConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
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
        return _workflow_response(result)

    @application.post(
        "/v1/investigations/runs/{thread_id}/review",
        response_model=InvestigationWorkflowResponse,
        tags=["investigations"],
    )
    async def review_workflow(
        thread_id: UUID,
        request: HumanReviewDecisionRequest,
    ) -> InvestigationWorkflowResponse:
        """Approve or reject exactly one persisted pending investigation."""

        decision = HumanReviewDecision(
            decision=request.decision,
            reviewer_id=request.reviewer_id,
            notes=request.notes,
        )
        try:
            if workflow_review_handler is not None:
                result = await workflow_review_handler(thread_id, decision)
            else:
                result = await run_workflow_review(
                    settings=resolved_settings,
                    thread_id=thread_id,
                    decision=decision,
                )
        except WorkflowNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except WorkflowStateConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return _workflow_response(result)

    @application.get(
        "/v1/investigations/runs/{thread_id}",
        response_model=InvestigationWorkflowResponse,
        tags=["investigations"],
    )
    async def get_workflow(thread_id: UUID) -> InvestigationWorkflowResponse:
        """Return a durable investigation for analyst workspace restoration."""

        try:
            result = (
                await workflow_get_handler(thread_id)
                if workflow_get_handler is not None
                else await run_workflow_get(settings=resolved_settings, thread_id=thread_id)
            )
        except WorkflowNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        return _workflow_response(result)

    @application.post(
        "/v1/investigations/runs/{thread_id}/feedback",
        response_model=InvestigationFeedbackResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["investigations"],
    )
    async def capture_feedback(
        thread_id: UUID,
        request: InvestigationFeedbackRequest,
    ) -> InvestigationFeedbackResponse:
        """Capture non-identifying feedback after a human review decision."""

        feedback = InvestigationFeedbackInput.model_validate(request.model_dump())
        try:
            result = (
                await feedback_handler(thread_id, feedback)
                if feedback_handler is not None
                else await run_feedback(
                    settings=resolved_settings,
                    thread_id=thread_id,
                    feedback=feedback,
                )
            )
        except WorkflowNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except (WorkflowStateConflictError, InvestigationFeedbackConflictError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return InvestigationFeedbackResponse.model_validate(result.model_dump())

    @application.post(
        "/v1/experiments/{experiment_key}/assignments",
        response_model=ExperimentAssignmentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["experiments"],
    )
    async def assign_experiment(
        experiment_key: str,
        request: ExperimentAssignmentRequest,
    ) -> ExperimentAssignmentResponse:
        """Return a privacy-preserving, persisted, sticky A/B assignment."""

        try:
            result = (
                await experiment_assignment_handler(experiment_key, request.unit_id)
                if experiment_assignment_handler is not None
                else await run_experiment_assignment(
                    settings=resolved_settings,
                    experiment_key=experiment_key,
                    unit_id=request.unit_id,
                )
            )
        except ExperimentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ExperimentContractError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return ExperimentAssignmentResponse.model_validate(result.model_dump())

    @application.post(
        "/v1/experiments/{experiment_key}/events",
        response_model=ExperimentEventResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["experiments"],
    )
    async def record_experiment_telemetry(
        experiment_key: str,
        request: ExperimentEventRequest,
    ) -> ExperimentEventResponse:
        """Record exactly one idempotent exposure or registered outcome."""

        event_input = ExperimentEventInput.model_validate(request.model_dump())
        try:
            result = (
                await experiment_event_handler(experiment_key, event_input)
                if experiment_event_handler is not None
                else await run_experiment_event(
                    settings=resolved_settings,
                    experiment_key=experiment_key,
                    event_input=event_input,
                )
            )
        except ExperimentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ExperimentContractError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return ExperimentEventResponse.model_validate(result.model_dump())

    @application.get(
        "/v1/experiments/{experiment_key}/analysis",
        response_model=ExperimentAnalysisReport,
        tags=["experiments"],
    )
    async def analyze_experiment(experiment_key: str) -> ExperimentAnalysisReport:
        """Return no-peeking experiment progress or terminal inference."""

        try:
            return (
                await experiment_analysis_handler(experiment_key)
                if experiment_analysis_handler is not None
                else await run_experiment_analysis(
                    settings=resolved_settings,
                    experiment_key=experiment_key,
                )
            )
        except ExperimentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ExperimentContractError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return application


app = create_app()
