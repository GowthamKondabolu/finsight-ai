"""Command-line interface for FinSight AI operations."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Collection, Sequence
from dataclasses import asdict

from finsight.config.settings import get_settings
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


def format_operation_result(
    result: SecIngestionResult | CompanyFactsIngestionResult | EmbeddingRunResult,
) -> str:
    """Serialize an operation result as readable JSON."""

    payload = asdict(result)
    if "company_id" in payload:
        payload["company_id"] = str(payload["company_id"])
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute a FinSight command."""

    parser = build_parser()
    arguments = parser.parse_args(argv)

    if arguments.command == "ingest-sec":
        forms: Collection[str] = (
            arguments.forms if arguments.forms is not None else DEFAULT_FILING_FORMS
        )
        result: SecIngestionResult | CompanyFactsIngestionResult | EmbeddingRunResult = asyncio.run(
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
    else:
        result = asyncio.run(
            run_embedding_backfill(
                cik=str(arguments.cik) if arguments.cik is not None else None,
                limit=int(arguments.limit),
            )
        )

    print(format_operation_result(result))
    return 0
