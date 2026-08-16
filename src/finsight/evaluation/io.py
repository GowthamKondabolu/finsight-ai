"""Bounded JSON input and output for offline evaluation artifacts."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel

from finsight.evaluation.contracts import (
    BenchmarkDataset,
    PairedExperimentReport,
    SystemRun,
)

MAX_EVALUATION_FILE_BYTES = 10 * 1024 * 1024


def _load_model[ModelType: BaseModel](
    path: Path,
    model_type: type[ModelType],
) -> ModelType:
    """Read and validate one bounded UTF-8 JSON artifact."""

    file_size = path.stat().st_size
    if file_size > MAX_EVALUATION_FILE_BYTES:
        raise ValueError(f"evaluation artifact exceeds {MAX_EVALUATION_FILE_BYTES} bytes: {path}")
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def load_dataset(path: Path) -> BenchmarkDataset:
    """Load a versioned benchmark dataset."""

    return _load_model(path, BenchmarkDataset)


def load_system_run(path: Path) -> SystemRun:
    """Load a recorded system run."""

    return _load_model(path, SystemRun)


def write_report(path: Path, report: PairedExperimentReport) -> None:
    """Atomically write a validated JSON report without partial output files."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump_json(indent=2) + "\n"
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary_path = Path(handle.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
