"""Command-line interface for FinSight AI operations."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Collection, Sequence
from dataclasses import asdict

from finsight.config.settings import get_settings
from finsight.ingestion.sec_client import SecEdgarClient
from finsight.ingestion.service import (
    DEFAULT_FILING_FORMS,
    MAX_FILINGS_PER_RUN,
    SecIngestionResult,
    ingest_company_filings,
)
from finsight.storage.database import (
    create_database_engine,
    create_session_factory,
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


def format_ingestion_result(result: SecIngestionResult) -> str:
    """Serialize an ingestion result as readable JSON."""

    payload = asdict(result)
    payload["company_id"] = str(result.company_id)
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute a FinSight command."""

    parser = build_parser()
    arguments = parser.parse_args(argv)

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
    print(format_ingestion_result(result))
    return 0
