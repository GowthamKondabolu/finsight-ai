"""Durable LangGraph investigation workflow with explicit human approval."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypedDict, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from finsight.agents.contracts import GroundedAnswerResult, InvestigationQuery
from finsight.config.settings import Settings

InvestigationExecutor = Callable[[InvestigationQuery], Awaitable[GroundedAnswerResult]]
WorkflowStatus = Literal["pending_review", "approved", "rejected"]

_ANSWER_ADAPTER = TypeAdapter(GroundedAnswerResult)


class HumanReviewDecision(BaseModel):
    """Auditable reviewer decision supplied when a paused graph resumes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["approve", "reject"]
    reviewer_id: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2_000)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("reviewer_id")
    @classmethod
    def normalize_reviewer_id(cls, value: str) -> str:
        """Reject an anonymous or whitespace-only reviewer identity."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("reviewer_id cannot be blank")
        return candidate

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        """Normalize an optional reviewer note without storing empty text."""

        if value is None:
            return None
        candidate = value.strip()
        return candidate or None

    @field_validator("decided_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Require an unambiguous audit timestamp."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decided_at must include a timezone")
        return value


class HumanReviewRequest(BaseModel):
    """Bounded evidence summary presented at the LangGraph interrupt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str
    answer_status: Literal["grounded", "insufficient_evidence", "needs_review"]
    answer: str
    source_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    review_reasons: tuple[str, ...]
    proposed_action: Literal["release_answer"] = "release_answer"


@dataclass(frozen=True, slots=True)
class InvestigationWorkflowResult:
    """Public state returned after starting or resuming one investigation."""

    thread_id: UUID
    status: WorkflowStatus
    answer: GroundedAnswerResult
    review_request: HumanReviewRequest | None
    review_decision: HumanReviewDecision | None


class WorkflowNotFoundError(LookupError):
    """Raised when a review attempts to resume an unknown thread."""


class WorkflowStateConflictError(RuntimeError):
    """Raised when a thread cannot accept the requested transition."""


class _WorkflowState(TypedDict, total=False):
    query: dict[str, object]
    answer: dict[str, object]
    workflow_status: WorkflowStatus
    review_decision: dict[str, object]


def _serialize_answer(answer: GroundedAnswerResult) -> dict[str, object]:
    """Convert an answer into checkpoint-safe JSON-compatible values."""

    return cast(dict[str, object], _ANSWER_ADAPTER.dump_python(answer, mode="json"))


def _deserialize_answer(payload: object) -> GroundedAnswerResult:
    """Restore and validate an answer loaded from a checkpoint."""

    return _ANSWER_ADAPTER.validate_python(payload)


def _review_request(answer: GroundedAnswerResult) -> HumanReviewRequest:
    """Create the minimal review packet exposed by the interrupt."""

    return HumanReviewRequest(
        question=answer.question,
        answer_status=answer.status,
        answer=answer.answer,
        source_ids=tuple(source.source_id for source in answer.sources),
        limitations=answer.limitations,
        review_reasons=answer.review_reasons,
    )


def build_investigation_workflow(
    *,
    executor: InvestigationExecutor,
    checkpointer: BaseCheckpointSaver[str],
) -> CompiledStateGraph[_WorkflowState, None, _WorkflowState, _WorkflowState]:
    """Compile the answer-and-review state machine with durable checkpoints."""

    async def investigate(state: _WorkflowState) -> _WorkflowState:
        query = InvestigationQuery.model_validate(state["query"])
        answer = await executor(query)
        return {
            "answer": _serialize_answer(answer),
            "workflow_status": "pending_review",
        }

    def request_human_review(state: _WorkflowState) -> _WorkflowState:
        answer = _deserialize_answer(state["answer"])
        request = _review_request(answer)
        raw_decision = interrupt(request.model_dump(mode="json"))
        decision = HumanReviewDecision.model_validate(raw_decision)
        return {
            "review_decision": decision.model_dump(mode="json"),
            "workflow_status": "approved" if decision.decision == "approve" else "rejected",
        }

    builder = StateGraph(_WorkflowState)
    builder.add_node("investigate", investigate)
    builder.add_node("human_review", request_human_review)
    builder.add_edge(START, "investigate")
    builder.add_edge("investigate", "human_review")
    builder.add_edge("human_review", END)
    return builder.compile(
        checkpointer=checkpointer,
        name="finsight-investigation-review",
    )


class InvestigationWorkflow:
    """Start and resume one compiled investigation graph."""

    def __init__(
        self,
        *,
        executor: InvestigationExecutor,
        checkpointer: BaseCheckpointSaver[str],
    ) -> None:
        self._graph = build_investigation_workflow(
            executor=executor,
            checkpointer=checkpointer,
        )

    @staticmethod
    def _config(thread_id: UUID) -> RunnableConfig:
        return {"configurable": {"thread_id": str(thread_id)}}

    async def start(
        self,
        *,
        thread_id: UUID,
        query: InvestigationQuery,
    ) -> InvestigationWorkflowResult:
        """Generate a guarded answer and pause before it can be released."""

        config = self._config(thread_id)
        existing = await self._graph.aget_state(config)
        if existing.values:
            raise WorkflowStateConflictError("workflow thread already exists")

        initial_state = cast(
            _WorkflowState,
            {"query": query.model_dump(mode="json")},
        )
        await self._graph.ainvoke(initial_state, config)
        return await self._result(thread_id)

    async def resume(
        self,
        *,
        thread_id: UUID,
        decision: HumanReviewDecision,
    ) -> InvestigationWorkflowResult:
        """Resume exactly one pending review with an auditable decision."""

        config = self._config(thread_id)
        snapshot = await self._graph.aget_state(config)
        if not snapshot.values:
            raise WorkflowNotFoundError("workflow thread was not found")
        if not snapshot.interrupts:
            raise WorkflowStateConflictError("workflow is not awaiting human review")

        command: Command[object] = Command(resume=decision.model_dump(mode="json"))
        await self._graph.ainvoke(command, config)
        return await self._result(thread_id)

    async def get(self, *, thread_id: UUID) -> InvestigationWorkflowResult:
        """Return one persisted workflow without changing its state."""

        snapshot = await self._graph.aget_state(self._config(thread_id))
        if not snapshot.values:
            raise WorkflowNotFoundError("workflow thread was not found")
        return await self._result(thread_id)

    async def _result(self, thread_id: UUID) -> InvestigationWorkflowResult:
        """Validate persisted graph values before crossing the service boundary."""

        snapshot = await self._graph.aget_state(self._config(thread_id))
        values = cast(_WorkflowState, snapshot.values)
        answer = _deserialize_answer(values["answer"])
        status = values.get("workflow_status", "pending_review")
        raw_decision = values.get("review_decision")
        decision = (
            HumanReviewDecision.model_validate(raw_decision) if raw_decision is not None else None
        )
        review_request = _review_request(answer) if status == "pending_review" else None
        return InvestigationWorkflowResult(
            thread_id=thread_id,
            status=status,
            answer=answer,
            review_request=review_request,
            review_decision=decision,
        )


def _checkpoint_connection_string(settings: Settings) -> str:
    """Convert the SQLAlchemy URL into the Psycopg URL used by the checkpointer."""

    database_url = settings.database_url.get_secret_value()
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@asynccontextmanager
async def postgres_investigation_workflow(
    *,
    settings: Settings,
    executor: InvestigationExecutor,
) -> AsyncIterator[InvestigationWorkflow]:
    """Open a PostgreSQL-backed workflow and apply checkpointer migrations."""

    connection_string = _checkpoint_connection_string(settings)
    async with AsyncPostgresSaver.from_conn_string(connection_string) as checkpointer:
        await checkpointer.setup()
        yield InvestigationWorkflow(
            executor=executor,
            checkpointer=checkpointer,
        )
