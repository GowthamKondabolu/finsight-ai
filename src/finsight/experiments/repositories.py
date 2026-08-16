"""PostgreSQL repositories for controlled experiment state and telemetry."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from finsight.experiments.analysis import AssignmentObservation, analyze_experiment
from finsight.experiments.assignment import (
    hash_randomization_unit,
    select_variant,
    validate_planned_sample_size,
)
from finsight.experiments.contracts import (
    AssignmentResult,
    EventType,
    ExperimentAnalysisReport,
    ExperimentContractError,
    ExperimentEventInput,
    ExperimentEventResult,
    ExperimentNotFoundError,
    ExperimentPlan,
    ExperimentStatus,
)
from finsight.storage.models import (
    Experiment,
    ExperimentAssignment,
    ExperimentEvent,
    ExperimentVariant,
)


async def _get_experiment(session: AsyncSession, experiment_key: str) -> Experiment:
    statement = (
        select(Experiment)
        .where(Experiment.experiment_key == experiment_key)
        .options(selectinload(Experiment.variants))
    )
    experiment = (await session.execute(statement)).scalar_one_or_none()
    if experiment is None:
        raise ExperimentNotFoundError(f"experiment '{experiment_key}' was not found")
    return experiment


def _plan(experiment: Experiment) -> ExperimentPlan:
    return ExperimentPlan.model_validate(experiment.plan)


async def register_experiment(
    session: AsyncSession,
    plan: ExperimentPlan,
) -> tuple[Experiment, bool]:
    """Register an immutable plan, treating exact repeats as idempotent."""

    validate_planned_sample_size(plan)
    existing = (
        await session.execute(
            select(Experiment)
            .where(Experiment.experiment_key == plan.experiment_key)
            .options(selectinload(Experiment.variants))
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.plan_fingerprint != plan.fingerprint():
            raise ExperimentContractError(
                "an experiment key cannot be reused for a different preregistered plan"
            )
        return existing, False

    experiment = Experiment(
        experiment_key=plan.experiment_key,
        name=plan.name,
        status="draft",
        plan_fingerprint=plan.fingerprint(),
        plan=plan.model_dump(mode="json"),
        variants=[
            ExperimentVariant(
                variant_key=variant.variant_key,
                allocation_basis_points=variant.allocation_basis_points,
                is_control=variant.is_control,
                configuration=variant.configuration,
            )
            for variant in plan.variants
        ],
    )
    session.add(experiment)
    await session.flush()
    return experiment, True


ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"running"}),
    "running": frozenset({"stopped", "completed"}),
    "stopped": frozenset({"completed"}),
    "completed": frozenset(),
}


async def transition_experiment_status(
    session: AsyncSession,
    *,
    experiment_key: str,
    target_status: ExperimentStatus,
    changed_at: datetime | None = None,
) -> Experiment:
    """Advance an experiment lifecycle without permitting backward transitions."""

    experiment = await _get_experiment(session, experiment_key)
    if experiment.status == target_status:
        return experiment
    if target_status not in ALLOWED_STATUS_TRANSITIONS[experiment.status]:
        raise ExperimentContractError(
            f"experiment cannot transition from {experiment.status} to {target_status}"
        )
    timestamp = changed_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ExperimentContractError("experiment transition timestamps require a timezone")
    experiment.status = target_status
    if target_status == "running":
        experiment.started_at = timestamp
    if target_status in {"stopped", "completed"}:
        experiment.ended_at = timestamp
    await session.flush()
    return experiment


def _assignment_result(
    assignment: ExperimentAssignment,
    *,
    experiment_key: str,
    existing: bool,
) -> AssignmentResult:
    return AssignmentResult(
        assignment_id=assignment.id,
        experiment_key=experiment_key,
        variant_key=assignment.variant.variant_key,
        variant_configuration=assignment.variant.configuration,
        assigned_at=assignment.assigned_at,
        existing_assignment=existing,
    )


async def assign_experiment_variant(
    session: AsyncSession,
    *,
    experiment_key: str,
    unit_id: str,
    assignment_secret: str,
    now: datetime | None = None,
) -> AssignmentResult:
    """Return a persisted sticky assignment without storing the raw unit ID."""

    experiment = await _get_experiment(session, experiment_key)
    plan = _plan(experiment)
    timestamp = now or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ExperimentContractError("assignment timestamps require a timezone")
    if experiment.status != "running":
        raise ExperimentContractError("experiment is not accepting assignments")
    if plan.starts_at is not None and timestamp < plan.starts_at:
        raise ExperimentContractError("experiment has not reached its scheduled start")
    if plan.ends_at is not None and timestamp >= plan.ends_at:
        raise ExperimentContractError("experiment assignment window has ended")

    unit_hash = hash_randomization_unit(
        secret=assignment_secret,
        experiment_key=plan.experiment_key,
        assignment_salt_version=plan.assignment_salt_version,
        unit_id=unit_id,
    )
    existing_statement = (
        select(ExperimentAssignment)
        .where(
            ExperimentAssignment.experiment_id == experiment.id,
            ExperimentAssignment.unit_hash == unit_hash,
        )
        .options(selectinload(ExperimentAssignment.variant))
    )
    existing = (await session.execute(existing_statement)).scalar_one_or_none()
    if existing is not None:
        return _assignment_result(existing, experiment_key=experiment_key, existing=True)

    selected = select_variant(plan, unit_hash)
    variant = next(item for item in experiment.variants if item.variant_key == selected.variant_key)
    assignment = ExperimentAssignment(
        experiment_id=experiment.id,
        variant_id=variant.id,
        unit_hash=unit_hash,
        assigned_at=timestamp,
    )
    assignment.variant = variant
    session.add(assignment)
    await session.flush()
    return _assignment_result(assignment, experiment_key=experiment_key, existing=False)


async def _get_assignment(
    session: AsyncSession,
    *,
    experiment_key: str,
    assignment_id: UUID,
) -> ExperimentAssignment:
    statement = (
        select(ExperimentAssignment)
        .join(Experiment, Experiment.id == ExperimentAssignment.experiment_id)
        .where(
            Experiment.experiment_key == experiment_key,
            ExperimentAssignment.id == assignment_id,
        )
        .options(
            selectinload(ExperimentAssignment.experiment),
            selectinload(ExperimentAssignment.variant),
            selectinload(ExperimentAssignment.events),
        )
    )
    assignment = (await session.execute(statement)).scalar_one_or_none()
    if assignment is None:
        raise ExperimentNotFoundError("experiment assignment was not found")
    return assignment


def _event_result(
    event: ExperimentEvent,
    *,
    experiment_key: str,
    variant_key: str,
    duplicate: bool,
) -> ExperimentEventResult:
    return ExperimentEventResult(
        event_id=event.id,
        experiment_key=experiment_key,
        assignment_id=event.assignment_id,
        variant_key=variant_key,
        event_key=event.event_key,
        event_type=cast(EventType, event.event_type),
        recorded_at=event.created_at,
        duplicate=duplicate,
    )


async def record_experiment_event(
    session: AsyncSession,
    *,
    experiment_key: str,
    event_input: ExperimentEventInput,
) -> ExperimentEventResult:
    """Validate and append one idempotent exposure or outcome event."""

    assignment = await _get_assignment(
        session,
        experiment_key=experiment_key,
        assignment_id=event_input.assignment_id,
    )
    duplicate = next(
        (event for event in assignment.events if event.event_key == event_input.event_key),
        None,
    )
    if duplicate is not None:
        stored_value = float(duplicate.metric_value) if duplicate.metric_value is not None else None
        if (
            duplicate.event_type != event_input.event_type
            or duplicate.metric_name != event_input.metric_name
            or stored_value != event_input.metric_value
            or duplicate.occurred_at != event_input.occurred_at
            or duplicate.event_metadata != event_input.metadata
        ):
            raise ExperimentContractError("an event key cannot be reused for different telemetry")
        return _event_result(
            duplicate,
            experiment_key=experiment_key,
            variant_key=assignment.variant.variant_key,
            duplicate=True,
        )
    if event_input.occurred_at < assignment.assigned_at:
        raise ExperimentContractError("event cannot occur before assignment")

    plan = _plan(assignment.experiment)
    if event_input.event_type == "outcome":
        assert event_input.metric_name is not None
        assert event_input.metric_value is not None
        if event_input.metric_name not in plan.metric_names:
            raise ExperimentContractError("outcome metric was not preregistered")
        metric = next(
            item
            for item in (plan.primary_metric, *plan.guardrail_metrics)
            if item.metric_name == event_input.metric_name
        )
        if metric.kind == "binary" and event_input.metric_value not in {0.0, 1.0}:
            raise ExperimentContractError("binary outcomes must be zero or one")
        if not any(event.event_type == "exposure" for event in assignment.events):
            raise ExperimentContractError("an exposure must be recorded before outcomes")
        if any(
            event.event_type == "outcome" and event.metric_name == event_input.metric_name
            for event in assignment.events
        ):
            raise ExperimentContractError("the assignment already has this outcome metric")
    elif any(event.event_type == "exposure" for event in assignment.events):
        raise ExperimentContractError("the assignment already has an exposure event")

    event = ExperimentEvent(
        assignment_id=assignment.id,
        event_key=event_input.event_key,
        event_type=event_input.event_type,
        metric_name=event_input.metric_name,
        metric_value=(
            Decimal(str(event_input.metric_value)) if event_input.metric_value is not None else None
        ),
        occurred_at=event_input.occurred_at,
        event_metadata=event_input.metadata,
    )
    event.assignment = assignment
    session.add(event)
    await session.flush()
    return _event_result(
        event,
        experiment_key=experiment_key,
        variant_key=assignment.variant.variant_key,
        duplicate=False,
    )


async def analyze_registered_experiment(
    session: AsyncSession,
    *,
    experiment_key: str,
    generated_at: datetime | None = None,
) -> ExperimentAnalysisReport:
    """Load anonymous telemetry and apply the preregistered analysis contract."""

    experiment = await _get_experiment(session, experiment_key)
    assignment_statement = (
        select(ExperimentAssignment)
        .where(ExperimentAssignment.experiment_id == experiment.id)
        .options(
            selectinload(ExperimentAssignment.variant),
            selectinload(ExperimentAssignment.events),
        )
    )
    assignments = (await session.execute(assignment_statement)).scalars().all()
    observations = [
        AssignmentObservation(
            variant_key=assignment.variant.variant_key,
            exposed=any(event.event_type == "exposure" for event in assignment.events),
            metrics={
                event.metric_name: float(event.metric_value)
                for event in assignment.events
                if event.event_type == "outcome"
                and event.metric_name is not None
                and event.metric_value is not None
            },
        )
        for assignment in assignments
    ]
    return analyze_experiment(
        plan=_plan(experiment),
        status=cast(ExperimentStatus, experiment.status),
        observations=observations,
        generated_at=generated_at,
    )
