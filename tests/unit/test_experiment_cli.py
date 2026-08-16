"""Tests for experiment registration, lifecycle, and analysis commands."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import finsight.cli as cli_module
from finsight.experiments.contracts import (
    ExperimentAnalysisReport,
    ExperimentRegistrationResult,
    ExperimentStatusResult,
)
from finsight.storage.models import Experiment
from tests.unit.experiment_helpers import experiment_plan

NOW = datetime(2026, 8, 20, tzinfo=UTC)


@asynccontextmanager
async def fake_scope(_: object) -> AsyncIterator[Mock]:
    """Yield a stable mock session for CLI transaction tests."""

    yield Mock()


def registration_result() -> ExperimentRegistrationResult:
    return ExperimentRegistrationResult(
        experiment_key="answer-workflow-v1",
        plan_fingerprint="a" * 64,
        status="running",
        created=True,
        planned_sample_size_per_variant=400,
        estimated_sample_size_per_variant=388,
    )


def status_result() -> ExperimentStatusResult:
    return ExperimentStatusResult(
        experiment_key="answer-workflow-v1",
        status="completed",
        started_at=NOW,
        ended_at=NOW,
    )


def analysis_result() -> ExperimentAnalysisReport:
    return ExperimentAnalysisReport(
        experiment_key="answer-workflow-v1",
        plan_fingerprint="a" * 64,
        status="running",
        generated_at=NOW,
        planned_sample_size_per_variant=400,
        exposed_assignments={"control": 10, "treatment": 10},
        analysis_ready=False,
        decision="collecting",
        primary_comparison=None,
        guardrail_comparisons=(),
        limitations=("No peeking.",),
    )


@pytest.mark.asyncio
async def test_registration_runner_starts_plan_and_releases_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Registration should be transactional and optionally start the draft."""

    plan = experiment_plan()
    experiment = Experiment(
        experiment_key=plan.experiment_key,
        name=plan.name,
        status="draft",
        plan_fingerprint=plan.fingerprint(),
        plan=plan.model_dump(mode="json"),
    )
    running = Experiment(
        experiment_key=plan.experiment_key,
        name=plan.name,
        status="running",
        plan_fingerprint=plan.fingerprint(),
        plan=plan.model_dump(mode="json"),
    )
    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(cli_module, "load_experiment_plan", Mock(return_value=plan))
    monkeypatch.setattr(cli_module, "get_settings", Mock(return_value=Mock()))
    monkeypatch.setattr(cli_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(cli_module, "create_session_factory", Mock(return_value=Mock()))
    monkeypatch.setattr(cli_module, "session_scope", fake_scope)
    monkeypatch.setattr(
        cli_module,
        "register_experiment",
        AsyncMock(return_value=(experiment, True)),
    )
    transition = AsyncMock(return_value=running)
    monkeypatch.setattr(cli_module, "transition_experiment_status", transition)

    result = await cli_module.run_experiment_registration(
        spec_path=tmp_path / "plan.json",
        start=True,
    )

    assert result.status == "running"
    assert result.estimated_sample_size_per_variant == 388
    transition.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_status_and_analysis_runners_release_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifecycle and analysis operations should own bounded database resources."""

    engine = MagicMock()
    engine.dispose = AsyncMock()
    experiment = Mock(
        experiment_key="answer-workflow-v1",
        status="completed",
        started_at=NOW,
        ended_at=NOW,
    )
    monkeypatch.setattr(cli_module, "get_settings", Mock(return_value=Mock()))
    monkeypatch.setattr(cli_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(cli_module, "create_session_factory", Mock(return_value=Mock()))
    monkeypatch.setattr(cli_module, "session_scope", fake_scope)
    transition = AsyncMock(return_value=experiment)
    analyze = AsyncMock(return_value=analysis_result())
    monkeypatch.setattr(cli_module, "transition_experiment_status", transition)
    monkeypatch.setattr(cli_module, "analyze_registered_experiment", analyze)

    status = await cli_module.run_experiment_status_transition(
        experiment_key="answer-workflow-v1",
        target_status="completed",
    )
    report = await cli_module.run_registered_experiment_analysis(
        experiment_key="answer-workflow-v1"
    )

    assert status.status == "completed"
    assert report.decision == "collecting"
    assert engine.dispose.await_count == 2


@pytest.mark.parametrize(
    ("arguments", "attribute", "result"),
    [
        (
            ["register-experiment", "--spec", "plan.json", "--start"],
            "run_experiment_registration",
            registration_result(),
        ),
        (
            [
                "set-experiment-status",
                "--experiment-key",
                "answer-workflow-v1",
                "--status",
                "completed",
            ],
            "run_experiment_status_transition",
            status_result(),
        ),
        (
            ["analyze-experiment", "--experiment-key", "answer-workflow-v1"],
            "run_registered_experiment_analysis",
            analysis_result(),
        ),
    ],
)
def test_main_dispatches_experiment_commands_and_prints_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    attribute: str,
    result: object,
) -> None:
    """All experiment management commands should emit machine-readable results."""

    operation = AsyncMock(return_value=result)
    monkeypatch.setattr(cli_module, attribute, operation)

    assert cli_module.main(arguments) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["experiment_key"] == "answer-workflow-v1"
    operation.assert_awaited_once()
