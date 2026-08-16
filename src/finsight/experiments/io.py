"""Bounded JSON input for immutable experiment plans."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from finsight.experiments.contracts import ExperimentContractError, ExperimentPlan

MAX_EXPERIMENT_PLAN_BYTES = 1_000_000


def load_experiment_plan(path: Path) -> ExperimentPlan:
    """Load a bounded UTF-8 JSON plan under the strict typed contract."""

    try:
        if path.stat().st_size > MAX_EXPERIMENT_PLAN_BYTES:
            raise ExperimentContractError("experiment plan exceeds the one-megabyte limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ExperimentPlan.model_validate(payload)
    except OSError as exc:
        raise ExperimentContractError(f"could not read experiment plan: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ExperimentContractError("experiment plan must be valid JSON") from exc
    except ValidationError as exc:
        raise ExperimentContractError("experiment plan failed schema validation") from exc
