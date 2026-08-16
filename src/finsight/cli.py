"""Command-line interface for FinSight AI operations."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Collection, Sequence
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel

from finsight.config.settings import get_settings
from finsight.evaluation.contracts import PairedExperimentReport
from finsight.evaluation.io import load_dataset, load_system_run, write_report
from finsight.evaluation.runner import (
    DEFAULT_BOOTSTRAP_ITERATIONS,
    DEFAULT_RANDOM_SEED,
    compare_systems,
)
from finsight.ingestion.company_facts_service import (
    DEFAULT_COMPANY_FACT_TAXONOMIES,
    CompanyFactsIngestionResult,
    ingest_company_facts,
)
from finsight.ingestion.sec_client import SecEdgarClient
from finsight.ingestion.service import (
    DEFAULT_FILING_FORMS,
    MAX_FILINGS_PER_RUN,
    SecIngestionResult,
    ingest_company_filings,
)
from finsight.retrieval.embedding_service import (
    MAX_EMBEDDING_CHUNKS_PER_RUN,
    EmbeddingRunResult,
    embed_pending_chunks,
)
from finsight.retrieval.embeddings import OpenAIEmbeddingProvider
from finsight.storage.database import (
    create_database_engine,
    create_session_factory,
)

OperationResult = (
    SecIngestionResult | CompanyFactsIngestionResult | EmbeddingRunResult | PairedExperimentReport
)


def build_parser() -> argparse.ArgumentParser:
    """Build the FinSight command-line argument parser."""

    parser = argparse.ArgumentParser(
        prog="finsight",
        description="FinSight AI financial-risk intelligence operations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser(
        "ingest-sec",
        help="Ingest recent SEC filings for one company.",
    )
    ingest_parser.add_argument(
        "--cik",
        required=True,
        help="SEC Central Index Key, with or without leading zeros.",
    )
    ingest_parser.add_argument(
        "--form",
        dest="forms",
        action="append",
        default=None,
        help="SEC form type to ingest. Repeat for multiple forms.",
    )
    ingest_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help=f"Maximum filings to ingest, from 1 to {MAX_FILINGS_PER_RUN}.",
    )

    facts_parser = subparsers.add_parser(
        "ingest-company-facts",
        help="Ingest normalized SEC XBRL company facts for one issuer.",
    )
    facts_parser.add_argument(
        "--cik",
        required=True,
        help="SEC Central Index Key, with or without leading zeros.",
    )
    facts_parser.add_argument(
        "--taxonomy",
        dest="taxonomies",
        action="append",
        default=None,
        help="XBRL taxonomy to ingest. Repeat for multiple taxonomies.",
    )

    embeddings_parser = subparsers.add_parser(
        "embed-chunks",
        help="Generate embeddings for missing or stale filing chunks.",
    )
    embeddings_parser.add_argument(
        "--cik",
        default=None,
        help="Optional SEC CIK used to restrict the embedding backfill.",
    )
    embeddings_parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help=(f"Maximum chunks to embed, from 1 to {MAX_EMBEDDING_CHUNKS_PER_RUN}."),
    )

    evaluation_parser = subparsers.add_parser(
        "evaluate",
        help="Compare recorded control and treatment runs on a versioned dataset.",
    )
    evaluation_parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to the versioned benchmark dataset JSON file.",
    )
    evaluation_parser.add_argument(
        "--control-run",
        type=Path,
        required=True,
        help="Path to the control system-run JSON file.",
    )
    evaluation_parser.add_argument(
        "--treatment-run",
        type=Path,
        required=True,
        help="Path to the treatment system-run JSON file.",
    )
    evaluation_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination for the paired evaluation report.",
    )
    evaluation_parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Retrieval cutoff used for recall and nDCG metrics.",
    )
    evaluation_parser.add_argument(
        "--bootstrap-iterations",
        type=int,
        default=DEFAULT_BOOTSTRAP_ITERATIONS,
        help="Paired bootstrap iterations, from 100 to 100000.",
    )
    evaluation_parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_RANDOM_SEED,
        help="Deterministic random seed for paired confidence intervals.",
    )

    return parser


async def run_sec_ingestion(
    *,
    cik: str,
    forms: Collection[str],
    limit: int,
) -> SecIngestionResult:
    """Run SEC ingestion and release all network and database resources."""

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with SecEdgarClient(settings) as client:
            return await ingest_company_filings(
                client=client,
                session_factory=session_factory,
                cik=cik,
                forms=forms,
                limit=limit,
            )
    finally:
        await engine.dispose()


async def run_company_facts_ingestion(
    *,
    cik: str,
    taxonomies: Collection[str],
) -> CompanyFactsIngestionResult:
    """Run company-facts ingestion and release network and database resources."""

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with SecEdgarClient(settings) as client:
            return await ingest_company_facts(
                client=client,
                session_factory=session_factory,
                cik=cik,
                taxonomies=taxonomies,
            )
    finally:
        await engine.dispose()


async def run_embedding_backfill(
    *,
    cik: str | None,
    limit: int,
) -> EmbeddingRunResult:
    """Generate chunk embeddings and release API and database resources."""

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with OpenAIEmbeddingProvider.from_settings(settings) as provider:
            return await embed_pending_chunks(
                provider=provider,
                session_factory=session_factory,
                limit=limit,
                batch_size=min(settings.embedding_batch_size, limit),
                cik=cik,
            )
    finally:
        await engine.dispose()


def run_paired_evaluation(
    *,
    dataset_path: Path,
    control_run_path: Path,
    treatment_run_path: Path,
    output_path: Path,
    top_k: int,
    bootstrap_iterations: int,
    random_seed: int,
) -> PairedExperimentReport:
    """Load, compare, and persist one versioned paired evaluation."""

    report = compare_systems(
        load_dataset(dataset_path),
        load_system_run(control_run_path),
        load_system_run(treatment_run_path),
        top_k=top_k,
        bootstrap_iterations=bootstrap_iterations,
        random_seed=random_seed,
    )
    write_report(output_path, report)
    return report


def format_operation_result(
    result: OperationResult,
) -> str:
    """Serialize an operation result as readable JSON."""

    payload = result.model_dump(mode="json") if isinstance(result, BaseModel) else asdict(result)
    if "company_id" in payload:
        payload["company_id"] = str(payload["company_id"])
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute a FinSight command."""

    parser = build_parser()
    arguments = parser.parse_args(argv)
    result: OperationResult

    if arguments.command == "ingest-sec":
        forms: Collection[str] = (
            arguments.forms if arguments.forms is not None else DEFAULT_FILING_FORMS
        )
        result = asyncio.run(
            run_sec_ingestion(
                cik=str(arguments.cik),
                forms=forms,
                limit=int(arguments.limit),
            )
        )
    elif arguments.command == "ingest-company-facts":
        taxonomies: Collection[str] = (
            arguments.taxonomies
            if arguments.taxonomies is not None
            else DEFAULT_COMPANY_FACT_TAXONOMIES
        )
        result = asyncio.run(
            run_company_facts_ingestion(
                cik=str(arguments.cik),
                taxonomies=taxonomies,
            )
        )
    elif arguments.command == "embed-chunks":
        result = asyncio.run(
            run_embedding_backfill(
                cik=str(arguments.cik) if arguments.cik is not None else None,
                limit=int(arguments.limit),
            )
        )
    else:
        result = run_paired_evaluation(
            dataset_path=arguments.dataset,
            control_run_path=arguments.control_run,
            treatment_run_path=arguments.treatment_run,
            output_path=arguments.output,
            top_k=int(arguments.top_k),
            bootstrap_iterations=int(arguments.bootstrap_iterations),
            random_seed=int(arguments.seed),
        )

    print(format_operation_result(result))
    return 0
