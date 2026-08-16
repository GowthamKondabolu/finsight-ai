"""PostgreSQL integration test for controlled experiment persistence."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from finsight.config.settings import Settings
from finsight.experiments.contracts import ExperimentEventInput
from finsight.experiments.repositories import (
    analyze_registered_experiment,
    assign_experiment_variant,
    record_experiment_event,
    register_experiment,
    transition_experiment_status,
)
from finsight.storage.database import create_database_engine, create_session_factory, session_scope
from finsight.storage.models import Experiment, ExperimentAssignment
from tests.unit.experiment_helpers import experiment_plan

RUN_DATABASE_TESTS = os.getenv("FINSIGHT_RUN_DATABASE_TESTS") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason="set FINSIGHT_RUN_DATABASE_TESTS=1 to run database integration tests",
    ),
]


@pytest.mark.asyncio
async def test_controlled_experiment_is_sticky_private_and_idempotent() -> None:
    """The real schema should persist one anonymous assignment and deduplicated telemetry."""

    now = datetime.now(UTC)
    plan = experiment_plan(
        experiment_key="integration-answer-workflow-v1",
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=1),
    )
    settings = Settings(environment="test")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_scope(session_factory) as session:
            await session.execute(
                delete(Experiment).where(Experiment.experiment_key == plan.experiment_key)
            )

        async with session_scope(session_factory) as session:
            _, created = await register_experiment(session, plan)
            await transition_experiment_status(
                session,
                experiment_key=plan.experiment_key,
                target_status="running",
                changed_at=now,
            )
            first = await assign_experiment_variant(
                session,
                experiment_key=plan.experiment_key,
                unit_id="raw-integration-session",
                assignment_secret=settings.experiment_assignment_secret.get_secret_value(),
                now=now,
            )
            repeated = await assign_experiment_variant(
                session,
                experiment_key=plan.experiment_key,
                unit_id="raw-integration-session",
                assignment_secret=settings.experiment_assignment_secret.get_secret_value(),
                now=now,
            )
            exposure_input = ExperimentEventInput(
                assignment_id=first.assignment_id,
                event_key="integration:exposure",
                event_type="exposure",
                occurred_at=now,
            )
            exposure = await record_experiment_event(
                session,
                experiment_key=plan.experiment_key,
                event_input=exposure_input,
            )
            duplicate = await record_experiment_event(
                session,
                experiment_key=plan.experiment_key,
                event_input=exposure_input,
            )
            await record_experiment_event(
                session,
                experiment_key=plan.experiment_key,
                event_input=ExperimentEventInput(
                    assignment_id=first.assignment_id,
                    event_key="integration:outcome",
                    event_type="outcome",
                    metric_name="verified_task_completion",
                    metric_value=1.0,
                    occurred_at=now + timedelta(seconds=1),
                ),
            )

            assert created is True
            assert repeated.assignment_id == first.assignment_id
            assert repeated.existing_assignment is True
            assert exposure.duplicate is False
            assert duplicate.duplicate is True

        async with session_scope(session_factory) as session:
            assignment = (
                await session.execute(
                    select(ExperimentAssignment).where(
                        ExperimentAssignment.id == first.assignment_id
                    )
                )
            ).scalar_one()
            report = await analyze_registered_experiment(
                session,
                experiment_key=plan.experiment_key,
                generated_at=now,
            )
            assert len(assignment.unit_hash) == 64
            assert "raw-integration-session" not in assignment.unit_hash
            assert report.decision == "collecting"
    finally:
        async with session_scope(session_factory) as session:
            await session.execute(
                delete(Experiment).where(Experiment.experiment_key == plan.experiment_key)
            )
        await engine.dispose()
