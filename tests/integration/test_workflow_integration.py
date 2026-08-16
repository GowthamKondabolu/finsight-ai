"""PostgreSQL integration test for durable LangGraph human review."""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection

from finsight.agents.contracts import GroundedAnswerResult, InvestigationQuery
from finsight.agents.workflow import (
    HumanReviewDecision,
    InvestigationWorkflow,
)
from finsight.config.settings import Settings

RUN_DATABASE_TESTS = os.getenv("FINSIGHT_RUN_DATABASE_TESTS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason="set FINSIGHT_RUN_DATABASE_TESTS=1 to run database integration tests",
    ),
]


def answer_result() -> GroundedAnswerResult:
    """Return a minimal safe answer for persistence validation."""

    return GroundedAnswerResult(
        question="What changed?",
        status="insufficient_evidence",
        answer="The available SEC evidence is insufficient to produce a grounded answer.",
        claims=(),
        numerical_validations=(),
        sources=(),
        limitations=("No filing passages matched the bounded retrieval request.",),
        model_name=None,
        requires_human_review=True,
        review_reasons=(
            "financial analysis requires qualified human review",
            "insufficient retrieved evidence",
        ),
    )


@pytest.mark.asyncio
async def test_postgres_checkpoint_survives_new_workflow_instance() -> None:
    """A fresh process-equivalent graph should resume the persisted interrupt."""

    settings = Settings()
    connection_string = settings.database_url.get_secret_value().replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )
    thread_id = uuid4()
    first_executor = AsyncMock(return_value=answer_result())

    async with AsyncPostgresSaver.from_conn_string(connection_string) as checkpointer:
        await checkpointer.setup()
        first_workflow = InvestigationWorkflow(
            executor=first_executor,
            checkpointer=checkpointer,
        )
        pending = await first_workflow.start(
            thread_id=thread_id,
            query=InvestigationQuery(question="What changed?"),
        )

    second_executor = AsyncMock(side_effect=AssertionError("investigation must not rerun"))
    async with AsyncPostgresSaver.from_conn_string(connection_string) as checkpointer:
        second_workflow = InvestigationWorkflow(
            executor=second_executor,
            checkpointer=checkpointer,
        )
        completed = await second_workflow.resume(
            thread_id=thread_id,
            decision=HumanReviewDecision(
                decision="reject",
                reviewer_id="integration-test",
                decided_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
            ),
        )

    assert pending.status == "pending_review"
    assert completed.status == "rejected"
    first_executor.assert_awaited_once()
    second_executor.assert_not_awaited()

    async with await AsyncConnection.connect(connection_string, autocommit=True) as connection:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await connection.execute(
                f"DELETE FROM {table} WHERE thread_id = %s",
                (str(thread_id),),
            )
