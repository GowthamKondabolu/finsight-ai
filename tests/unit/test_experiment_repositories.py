"""Tests for experiment persistence orchestration without external infrastructure."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest

from finsight.experiments.contracts import ExperimentContractError, ExperimentEventInput
from finsight.experiments.repositories import (
    analyze_registered_experiment,
    assign_experiment_variant,
    record_experiment_event,
    register_experiment,
    transition_experiment_status,
)
from finsight.storage.models import (
    Experiment,
    ExperimentAssignment,
    ExperimentEvent,
    ExperimentVariant,
)
from tests.unit.experiment_helpers import experiment_plan

NOW = datetime(2026, 8, 20, tzinfo=UTC)
SECRET = "test-only-secret-with-at-least-32-characters"


def stored_experiment(status: str = "running") -> Experiment:
    """Build a populated ORM plan for repository unit tests."""

    plan = experiment_plan(starts_at=NOW - timedelta(days=1), ends_at=NOW + timedelta(days=1))
    experiment = Experiment(
        experiment_key=plan.experiment_key,
        name=plan.name,
        status=status,
        plan_fingerprint=plan.fingerprint(),
        plan=plan.model_dump(mode="json"),
    )
    experiment.id = UUID("11111111-1111-4111-8111-111111111111")
    experiment.variants = [
        ExperimentVariant(
            experiment_id=experiment.id,
            variant_key=variant.variant_key,
            allocation_basis_points=variant.allocation_basis_points,
            is_control=variant.is_control,
            configuration=variant.configuration,
        )
        for variant in plan.variants
    ]
    for index, variant in enumerate(experiment.variants, start=1):
        variant.id = UUID(f"22222222-2222-4222-8222-{index:012d}")
        variant.experiment = experiment
    return experiment


def async_session(result: object | None = None) -> AsyncMock:
    """Return a minimal typed-looking session mock."""

    session = AsyncMock()
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = result
    session.execute.return_value = query_result
    session.add = Mock()
    return session


@pytest.mark.asyncio
async def test_registration_is_idempotent_and_rejects_key_reuse() -> None:
    """Exact plans may repeat, but a stable key cannot identify changed configuration."""

    plan = experiment_plan()
    session = async_session()
    experiment, created = await register_experiment(session, plan)

    assert created is True
    assert experiment.plan_fingerprint == plan.fingerprint()
    assert {variant.variant_key for variant in experiment.variants} == {"control", "treatment"}
    session.add.assert_called_once_with(experiment)
    session.flush.assert_awaited_once()

    existing_session = async_session(experiment)
    repeated, created = await register_experiment(existing_session, plan)
    assert repeated is experiment
    assert created is False

    changed = experiment_plan(name="Changed plan")
    with pytest.raises(ExperimentContractError, match="cannot be reused"):
        await register_experiment(existing_session, changed)


@pytest.mark.asyncio
async def test_registration_rejects_underpowered_plan() -> None:
    """Power validation must run before any database write."""

    with pytest.raises(ExperimentContractError, match="below the power estimate"):
        await register_experiment(
            async_session(),
            experiment_plan(planned_sample_size_per_variant=2),
        )


@pytest.mark.asyncio
async def test_lifecycle_transitions_are_one_way_and_idempotent() -> None:
    """Drafts can start once and terminal experiments cannot restart."""

    session = async_session()
    experiment = stored_experiment("draft")
    with patch(
        "finsight.experiments.repositories._get_experiment",
        AsyncMock(return_value=experiment),
    ):
        running = await transition_experiment_status(
            session,
            experiment_key=experiment.experiment_key,
            target_status="running",
            changed_at=NOW,
        )
        assert running.started_at == NOW
        repeated = await transition_experiment_status(
            session,
            experiment_key=experiment.experiment_key,
            target_status="running",
            changed_at=NOW,
        )
        assert repeated is experiment
        stopped = await transition_experiment_status(
            session,
            experiment_key=experiment.experiment_key,
            target_status="stopped",
            changed_at=NOW + timedelta(hours=1),
        )
        assert stopped.ended_at == NOW + timedelta(hours=1)
        with pytest.raises(ExperimentContractError, match="cannot transition"):
            await transition_experiment_status(
                session,
                experiment_key=experiment.experiment_key,
                target_status="running",
            )
        completed = await transition_experiment_status(
            session,
            experiment_key=experiment.experiment_key,
            target_status="completed",
        )
        assert completed.status == "completed"


@pytest.mark.asyncio
async def test_lifecycle_requires_timezone() -> None:
    """Lifecycle audit timestamps cannot be ambiguous."""

    with (
        patch(
            "finsight.experiments.repositories._get_experiment",
            AsyncMock(return_value=stored_experiment("draft")),
        ),
        pytest.raises(ExperimentContractError, match="require a timezone"),
    ):
        await transition_experiment_status(
            async_session(),
            experiment_key="answer-workflow-v1",
            target_status="running",
            changed_at=datetime(2026, 8, 20),
        )


@pytest.mark.asyncio
async def test_assignment_is_sticky_and_does_not_persist_raw_unit() -> None:
    """Only a scoped HMAC should reach the assignment record."""

    experiment = stored_experiment()
    session = async_session()

    async def assign_identity() -> None:
        assignment = session.add.call_args.args[0]
        assignment.id = UUID("33333333-3333-4333-8333-333333333333")

    session.flush.side_effect = assign_identity
    with patch(
        "finsight.experiments.repositories._get_experiment",
        AsyncMock(return_value=experiment),
    ):
        result = await assign_experiment_variant(
            session,
            experiment_key=experiment.experiment_key,
            unit_id="raw-session-id",
            assignment_secret=SECRET,
            now=NOW,
        )

    assignment = session.add.call_args.args[0]
    assert result.existing_assignment is False
    assert len(assignment.unit_hash) == 64
    assert "raw-session-id" not in assignment.unit_hash

    existing_session = async_session(assignment)
    with patch(
        "finsight.experiments.repositories._get_experiment",
        AsyncMock(return_value=experiment),
    ):
        repeated = await assign_experiment_variant(
            existing_session,
            experiment_key=experiment.experiment_key,
            unit_id="raw-session-id",
            assignment_secret=SECRET,
            now=NOW,
        )
    assert repeated.assignment_id == result.assignment_id
    assert repeated.existing_assignment is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "now", "message"),
    [
        ("draft", NOW, "not accepting"),
        ("running", NOW - timedelta(days=2), "scheduled start"),
        ("running", NOW + timedelta(days=2), "window has ended"),
    ],
)
async def test_assignment_enforces_lifecycle_and_schedule(
    status: str,
    now: datetime,
    message: str,
) -> None:
    """Units must not enter inactive or out-of-window experiments."""

    with (
        patch(
            "finsight.experiments.repositories._get_experiment",
            AsyncMock(return_value=stored_experiment(status)),
        ),
        pytest.raises(ExperimentContractError, match=message),
    ):
        await assign_experiment_variant(
            async_session(),
            experiment_key="answer-workflow-v1",
            unit_id="session",
            assignment_secret=SECRET,
            now=now,
        )


def stored_assignment(events: list[ExperimentEvent] | None = None) -> ExperimentAssignment:
    """Build an assignment with loaded experiment, variant, and events."""

    experiment = stored_experiment()
    assignment = ExperimentAssignment(
        experiment_id=experiment.id,
        variant_id=experiment.variants[0].id,
        unit_hash="b" * 64,
        assigned_at=NOW,
    )
    assignment.id = UUID("33333333-3333-4333-8333-333333333333")
    assignment.experiment = experiment
    assignment.variant = experiment.variants[0]
    assignment.events = events or []
    return assignment


def event(
    event_type: str, *, metric_name: str | None = None, value: float | None = None
) -> ExperimentEvent:
    """Build loaded telemetry for repository tests."""

    item = ExperimentEvent(
        assignment_id=UUID("33333333-3333-4333-8333-333333333333"),
        event_key=f"{event_type}:{metric_name or 'marker'}",
        event_type=event_type,
        metric_name=metric_name,
        metric_value=Decimal(str(value)) if value is not None else None,
        occurred_at=NOW,
        event_metadata={},
    )
    item.id = uuid4()
    item.created_at = NOW
    return item


@pytest.mark.asyncio
async def test_events_are_ordered_validated_and_idempotent() -> None:
    """Exposure precedes one outcome per metric and exact event repeats deduplicate."""

    assignment = stored_assignment()
    session = async_session()

    async def add_identity() -> None:
        stored = session.add.call_args.args[0]
        stored.id = uuid4()
        stored.created_at = NOW

    session.flush.side_effect = add_identity
    with patch(
        "finsight.experiments.repositories._get_assignment",
        AsyncMock(return_value=assignment),
    ):
        exposure_input = ExperimentEventInput(
            assignment_id=assignment.id,
            event_key="exposure:new",
            event_type="exposure",
            occurred_at=NOW,
        )
        exposure_result = await record_experiment_event(
            session,
            experiment_key="answer-workflow-v1",
            event_input=exposure_input,
        )
        assert exposure_result.duplicate is False

    exposure = session.add.call_args.args[0]
    assignment.events = [exposure]
    with patch(
        "finsight.experiments.repositories._get_assignment",
        AsyncMock(return_value=assignment),
    ):
        duplicate = await record_experiment_event(
            session,
            experiment_key="answer-workflow-v1",
            event_input=exposure_input,
        )
        assert duplicate.duplicate is True

        with pytest.raises(ExperimentContractError, match="cannot be reused"):
            await record_experiment_event(
                session,
                experiment_key="answer-workflow-v1",
                event_input=exposure_input.model_copy(
                    update={"occurred_at": NOW + timedelta(seconds=2)}
                ),
            )

        outcome_input = ExperimentEventInput(
            assignment_id=assignment.id,
            event_key="outcome:completion",
            event_type="outcome",
            metric_name="verified_task_completion",
            metric_value=1.0,
            occurred_at=NOW + timedelta(seconds=1),
        )
        outcome = await record_experiment_event(
            session,
            experiment_key="answer-workflow-v1",
            event_input=outcome_input,
        )
        assert outcome.event_type == "outcome"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing_events", "event_input", "message"),
    [
        (
            [],
            ExperimentEventInput(
                assignment_id=UUID("33333333-3333-4333-8333-333333333333"),
                event_key="outcome:no-exposure",
                event_type="outcome",
                metric_name="verified_task_completion",
                metric_value=1.0,
                occurred_at=NOW,
            ),
            "exposure must be recorded",
        ),
        (
            [event("exposure")],
            ExperimentEventInput(
                assignment_id=UUID("33333333-3333-4333-8333-333333333333"),
                event_key="outcome:unknown",
                event_type="outcome",
                metric_name="unknown_metric",
                metric_value=1.0,
                occurred_at=NOW,
            ),
            "not preregistered",
        ),
        (
            [event("exposure")],
            ExperimentEventInput(
                assignment_id=UUID("33333333-3333-4333-8333-333333333333"),
                event_key="outcome:binary",
                event_type="outcome",
                metric_name="verified_task_completion",
                metric_value=0.5,
                occurred_at=NOW,
            ),
            "zero or one",
        ),
        (
            [
                event("exposure"),
                event("outcome", metric_name="verified_task_completion", value=1.0),
            ],
            ExperimentEventInput(
                assignment_id=UUID("33333333-3333-4333-8333-333333333333"),
                event_key="outcome:second",
                event_type="outcome",
                metric_name="verified_task_completion",
                metric_value=0.0,
                occurred_at=NOW,
            ),
            "already has this outcome",
        ),
    ],
)
async def test_event_repository_rejects_contract_violations(
    existing_events: list[ExperimentEvent],
    event_input: ExperimentEventInput,
    message: str,
) -> None:
    """Unexposed, unplanned, malformed, and repeated outcomes must fail."""

    with (
        patch(
            "finsight.experiments.repositories._get_assignment",
            AsyncMock(return_value=stored_assignment(existing_events)),
        ),
        pytest.raises(ExperimentContractError, match=message),
    ):
        await record_experiment_event(
            async_session(),
            experiment_key="answer-workflow-v1",
            event_input=event_input,
        )


@pytest.mark.asyncio
async def test_event_repository_rejects_duplicate_exposure_and_preassignment_time() -> None:
    """One exposure is counted per assignment and timestamps cannot predate assignment."""

    duplicate_exposure = ExperimentEventInput(
        assignment_id=UUID("33333333-3333-4333-8333-333333333333"),
        event_key="exposure:second",
        event_type="exposure",
        occurred_at=NOW,
    )
    with (
        patch(
            "finsight.experiments.repositories._get_assignment",
            AsyncMock(return_value=stored_assignment([event("exposure")])),
        ),
        pytest.raises(ExperimentContractError, match="already has an exposure"),
    ):
        await record_experiment_event(
            async_session(),
            experiment_key="answer-workflow-v1",
            event_input=duplicate_exposure,
        )

    early = duplicate_exposure.model_copy(
        update={"event_key": "exposure:early", "occurred_at": NOW - timedelta(seconds=1)}
    )
    with (
        patch(
            "finsight.experiments.repositories._get_assignment",
            AsyncMock(return_value=stored_assignment()),
        ),
        pytest.raises(ExperimentContractError, match="before assignment"),
    ):
        await record_experiment_event(
            async_session(),
            experiment_key="answer-workflow-v1",
            event_input=early,
        )


@pytest.mark.asyncio
async def test_registered_analysis_maps_loaded_telemetry() -> None:
    """ORM rows should become anonymous analysis observations with no raw identity."""

    experiment = stored_experiment()
    assignment = stored_assignment(
        [
            event("exposure"),
            event("outcome", metric_name="verified_task_completion", value=1.0),
        ]
    )
    result = Mock()
    result.scalars.return_value.all.return_value = [assignment]
    session = async_session()
    session.execute.return_value = result
    with patch(
        "finsight.experiments.repositories._get_experiment",
        AsyncMock(return_value=experiment),
    ):
        report = await analyze_registered_experiment(
            session,
            experiment_key=experiment.experiment_key,
            generated_at=NOW,
        )
    assert report.decision == "collecting"
    assert report.exposed_assignments["control"] == 1
