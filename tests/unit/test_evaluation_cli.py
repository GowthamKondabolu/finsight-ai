"""Tests for the offline evaluation command-line workflow."""

import json
from pathlib import Path

import pytest

from finsight.cli import main
from finsight.evaluation.contracts import PairedExperimentReport

FIXTURES = Path("evals/fixtures")


def test_evaluation_cli_writes_report_and_prints_valid_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI should execute the committed fixture without network or database access."""

    output = tmp_path / "evaluation-report.json"
    exit_code = main(
        [
            "evaluate",
            "--dataset",
            str(FIXTURES / "synthetic_dataset_v1.json"),
            "--control-run",
            str(FIXTURES / "control_run_v1.json"),
            "--treatment-run",
            str(FIXTURES / "treatment_run_v1.json"),
            "--output",
            str(output),
            "--top-k",
            "3",
            "--bootstrap-iterations",
            "100",
            "--seed",
            "29",
        ]
    )

    assert exit_code == 0
    report = PairedExperimentReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert report.random_seed == 29
    assert report.control.top_k == 3
    assert report.benchmark_claim_allowed is False

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["dataset_id"] == "finsight-synthetic-contract-v1"
    assert payload["benchmark_claim_allowed"] is False
