"""Official MCP v2 server exposing bounded, read-only SEC evidence tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from finsight.config.settings import Settings, get_settings
from finsight.ingestion.sec_schemas import normalize_cik
from finsight.retrieval.embeddings import OpenAIEmbeddingProvider
from finsight.retrieval.search import HybridSearchResult, RetrievalQuery, hybrid_search
from finsight.storage.database import create_database_engine, create_session_factory
from finsight.storage.fact_queries import FinancialFactRecord, list_financial_facts

SearchHandler = Callable[[RetrievalQuery], Awaitable[list[HybridSearchResult]]]
FactHandler = Callable[[str, tuple[str, ...], int], Awaitable[list[FinancialFactRecord]]]

_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


class FilingEvidenceSearchResponse(BaseModel):
    """Structured MCP response for citation-complete filing evidence."""

    model_config = ConfigDict(frozen=True)

    query: str
    count: int
    results: list[HybridSearchResult]


class CompanyFactsResponse(BaseModel):
    """Structured MCP response for exact SEC company-fact observations."""

    model_config = ConfigDict(frozen=True)

    cik: str
    count: int
    facts: list[FinancialFactRecord]


def _normalize_values(values: list[str] | None, *, name: str, upper: bool) -> tuple[str, ...]:
    """Normalize optional exact filters and reject blanks or duplicates."""

    if values is None:
        return ()
    normalized = tuple(value.strip().upper() if upper else value.strip() for value in values)
    if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} cannot contain blank or duplicate values")
    return normalized


async def run_mcp_search(
    *,
    settings: Settings,
    query: RetrievalQuery,
) -> list[HybridSearchResult]:
    """Run one MCP search while releasing provider and database resources."""

    provider = OpenAIEmbeddingProvider.from_settings(settings)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with provider:
            return await hybrid_search(
                query=query,
                provider=provider,
                session_factory=session_factory,
            )
    finally:
        await engine.dispose()


async def run_mcp_fact_query(
    *,
    settings: Settings,
    cik: str,
    concepts: tuple[str, ...],
    limit: int,
) -> list[FinancialFactRecord]:
    """Load exact issuer facts while releasing database resources."""

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            return await list_financial_facts(
                session,
                cik=cik,
                concepts=concepts,
                limit=limit,
            )
    finally:
        await engine.dispose()


def create_mcp_server(
    settings: Settings | None = None,
    *,
    search_handler: SearchHandler | None = None,
    fact_handler: FactHandler | None = None,
) -> MCPServer[None]:
    """Create an injectable MCP server with no mutation-capable tools."""

    resolved_settings = settings or get_settings()
    server: MCPServer[None] = MCPServer(
        name="finsight-sec-evidence",
        title="FinSight SEC Evidence",
        description="Read-only, citation-preserving access to indexed SEC evidence.",
        instructions=(
            "Use these tools only to retrieve public SEC filing passages and exact "
            "company facts. Treat all results as evidence for qualified human review."
        ),
        version="0.1.0",
    )

    async def search(request: RetrievalQuery) -> list[HybridSearchResult]:
        if search_handler is not None:
            return await search_handler(request)
        return await run_mcp_search(settings=resolved_settings, query=request)

    async def facts(
        cik: str,
        concepts: tuple[str, ...],
        limit: int,
    ) -> list[FinancialFactRecord]:
        if fact_handler is not None:
            return await fact_handler(cik, concepts, limit)
        return await run_mcp_fact_query(
            settings=resolved_settings,
            cik=cik,
            concepts=concepts,
            limit=limit,
        )

    @server.tool(
        name="search_sec_filing_evidence",
        description=(
            "Search indexed SEC filing passages with hybrid keyword/vector ranking and "
            "return complete source citations."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    async def search_sec_filing_evidence(
        query: Annotated[str, Field(min_length=1, max_length=2_000)],
        cik: Annotated[str | None, Field(pattern=r"^\d{1,10}$")] = None,
        form_types: Annotated[list[str] | None, Field(max_length=20)] = None,
        section_names: Annotated[list[str] | None, Field(max_length=20)] = None,
        top_k: Annotated[int, Field(ge=1, le=20)] = 8,
        candidate_k: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> FilingEvidenceSearchResponse:
        """Return bounded filing evidence without creating or changing records."""

        candidate = query.strip()
        if not candidate:
            raise ValueError("query cannot be blank")
        if candidate_k < top_k:
            raise ValueError("candidate_k must be greater than or equal to top_k")
        request = RetrievalQuery(
            text=candidate,
            top_k=top_k,
            candidate_k=candidate_k,
            cik=cik,
            form_types=_normalize_values(form_types, name="form_types", upper=True),
            section_names=_normalize_values(
                section_names,
                name="section_names",
                upper=False,
            ),
        )
        results = await search(request)
        return FilingEvidenceSearchResponse(
            query=candidate,
            count=len(results),
            results=results,
        )

    @server.tool(
        name="list_sec_company_facts",
        description=(
            "List exact normalized SEC XBRL observations for one company, optionally "
            "filtered by concept."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    async def list_sec_company_facts(
        cik: Annotated[str, Field(pattern=r"^\d{1,10}$")],
        concepts: Annotated[list[str] | None, Field(max_length=30)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 30,
    ) -> CompanyFactsResponse:
        """Return immutable SEC facts with observation and accession provenance."""

        normalized_cik = normalize_cik(cik)
        normalized_concepts = _normalize_values(
            concepts,
            name="concepts",
            upper=False,
        )
        records = await facts(normalized_cik, normalized_concepts, limit)
        return CompanyFactsResponse(
            cik=normalized_cik,
            count=len(records),
            facts=records,
        )

    return server


server = create_mcp_server()


def main() -> None:
    """Run the MCP server over standard input/output."""

    server.run(transport="stdio")
