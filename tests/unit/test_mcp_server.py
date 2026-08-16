"""Tests for the official MCP v2 read-only SEC evidence server."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest
from mcp import Client

import finsight.mcp.server as mcp_module
from finsight.config.settings import Settings
from finsight.mcp.server import create_mcp_server
from finsight.retrieval.search import (
    HybridSearchResult,
    RetrievalCitation,
    RetrievalQuery,
)
from finsight.storage.fact_queries import FinancialFactRecord


def search_result() -> HybridSearchResult:
    """Return one citation-complete filing passage."""

    return HybridSearchResult(
        chunk_id=UUID("00000000-0000-4000-8000-000000000001"),
        content="Supply constraints may affect operations.",
        content_hash="a" * 64,
        score=0.0325,
        keyword_rank=1,
        semantic_rank=2,
        keyword_score=0.8,
        semantic_score=0.9,
        matched_by=("keyword", "semantic"),
        citation=RetrievalCitation(
            company_name="Apple Inc.",
            cik="0000320193",
            ticker="AAPL",
            accession_number="0000320193-25-000079",
            form_type="10-K",
            filing_date=date(2025, 10, 31),
            report_date=date(2025, 9, 27),
            section_name="Item 1A. Risk Factors",
            section_sequence=1,
            chunk_index=2,
            source_url="https://www.sec.gov/example",
        ),
        chunk_metadata={"token_start": 20},
    )


def fact_record() -> FinancialFactRecord:
    """Return one exact normalized financial fact."""

    return FinancialFactRecord(
        observation_key="b" * 64,
        concept="Revenue",
        label="Revenue",
        unit="USD",
        value=Decimal("100.25"),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        filed_date=date(2026, 2, 1),
        fiscal_year=2025,
        fiscal_period="FY",
        form_type="10-K",
        accession_number="0000320193-26-000001",
    )


@pytest.mark.asyncio
async def test_mcp_server_advertises_only_read_only_tools() -> None:
    """Every published tool should explicitly deny mutation semantics."""

    server = create_mcp_server(
        Settings(environment="test"),
        search_handler=AsyncMock(),
        fact_handler=AsyncMock(),
    )

    async with Client(server) as client:
        response = await client.list_tools()

    assert {tool.name for tool in response.tools} == {
        "search_sec_filing_evidence",
        "list_sec_company_facts",
    }
    for tool in response.tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False


@pytest.mark.asyncio
async def test_mcp_search_returns_structured_citation_evidence() -> None:
    """The search tool should retain ranks, hashes, and source location."""

    handler = AsyncMock(return_value=[search_result()])
    server = create_mcp_server(
        Settings(environment="test"),
        search_handler=handler,
        fact_handler=AsyncMock(),
    )

    async with Client(server) as client:
        response = await client.call_tool(
            "search_sec_filing_evidence",
            {
                "query": " supply risk ",
                "cik": "320193",
                "form_types": ["10-k"],
                "section_names": ["Item 1A. Risk Factors"],
                "top_k": 5,
                "candidate_k": 20,
            },
        )

    assert response.is_error is False
    payload = response.structured_content
    assert payload is not None
    assert payload["count"] == 1
    assert payload["results"][0]["citation"]["accession_number"] == ("0000320193-25-000079")
    assert payload["results"][0]["matched_by"] == ["keyword", "semantic"]
    handler.assert_awaited_once_with(
        RetrievalQuery(
            text="supply risk",
            top_k=5,
            candidate_k=20,
            cik="320193",
            form_types=("10-K",),
            section_names=("Item 1A. Risk Factors",),
        )
    )


@pytest.mark.asyncio
async def test_mcp_fact_tool_normalizes_cik_and_preserves_exact_value() -> None:
    """Fact results should remain issuer-scoped, exact, and attributable."""

    handler = AsyncMock(return_value=[fact_record()])
    server = create_mcp_server(
        Settings(environment="test"),
        search_handler=AsyncMock(),
        fact_handler=handler,
    )

    async with Client(server) as client:
        response = await client.call_tool(
            "list_sec_company_facts",
            {"cik": "320193", "concepts": ["Revenue"], "limit": 10},
        )

    assert response.is_error is False
    payload = response.structured_content
    assert payload is not None
    assert payload["cik"] == "0000320193"
    assert payload["facts"][0]["value"] == "100.25"
    assert payload["facts"][0]["accession_number"] == "0000320193-26-000001"
    handler.assert_awaited_once_with("0000320193", ("Revenue",), 10)


@pytest.mark.asyncio
async def test_default_mcp_tools_call_production_runners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An uninjected server should route both tools through managed resources."""

    search = AsyncMock(return_value=[search_result()])
    facts = AsyncMock(return_value=[fact_record()])
    monkeypatch.setattr(mcp_module, "run_mcp_search", search)
    monkeypatch.setattr(mcp_module, "run_mcp_fact_query", facts)
    settings = Settings(environment="test")
    server = create_mcp_server(settings)

    async with Client(server) as client:
        search_response = await client.call_tool(
            "search_sec_filing_evidence",
            {"query": "risk"},
        )
        fact_response = await client.call_tool(
            "list_sec_company_facts",
            {"cik": "320193"},
        )

    assert search_response.is_error is False
    assert fact_response.is_error is False
    search.assert_awaited_once_with(
        settings=settings,
        query=RetrievalQuery(text="risk", top_k=8, candidate_k=50),
    )
    facts.assert_awaited_once_with(
        settings=settings,
        cik="0000320193",
        concepts=(),
        limit=30,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "search_sec_filing_evidence",
            {"query": "risk", "top_k": 10, "candidate_k": 5},
        ),
        ("search_sec_filing_evidence", {"query": "   "}),
        (
            "search_sec_filing_evidence",
            {"query": "risk", "form_types": ["10-K", "10-k"]},
        ),
        (
            "search_sec_filing_evidence",
            {"query": "risk", "section_names": ["Risk", "Risk"]},
        ),
        (
            "list_sec_company_facts",
            {"cik": "320193", "concepts": ["Revenue", "Revenue"]},
        ),
    ],
)
async def test_mcp_tools_reject_ambiguous_bounds(
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """Agent callers cannot bypass result bounds or exact-filter contracts."""

    search_handler = AsyncMock()
    fact_handler = AsyncMock()
    server = create_mcp_server(
        Settings(environment="test"),
        search_handler=search_handler,
        fact_handler=fact_handler,
    )

    async with Client(server) as client:
        response = await client.call_tool(tool_name, arguments)

    assert response.is_error is True
    search_handler.assert_not_awaited()
    fact_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_production_mcp_search_releases_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search should close its embedding provider and database engine."""

    provider = MagicMock()
    provider.__aenter__ = AsyncMock(return_value=provider)
    provider.__aexit__ = AsyncMock(return_value=None)
    provider_factory = Mock()
    provider_factory.from_settings.return_value = provider
    engine = MagicMock()
    engine.dispose = AsyncMock()
    session_factory = Mock()
    search = AsyncMock(return_value=[search_result()])
    monkeypatch.setattr(mcp_module, "OpenAIEmbeddingProvider", provider_factory)
    monkeypatch.setattr(mcp_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(
        mcp_module,
        "create_session_factory",
        Mock(return_value=session_factory),
    )
    monkeypatch.setattr(mcp_module, "hybrid_search", search)
    query = RetrievalQuery(text="risk")

    result = await mcp_module.run_mcp_search(settings=MagicMock(), query=query)

    assert result == [search_result()]
    search.assert_awaited_once_with(
        query=query,
        provider=provider,
        session_factory=session_factory,
    )
    provider.__aexit__.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_production_mcp_fact_query_releases_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fact lookup should close its database engine after the bounded query."""

    engine = MagicMock()
    engine.dispose = AsyncMock()
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session_factory = Mock(return_value=session)
    query = AsyncMock(return_value=[fact_record()])
    monkeypatch.setattr(mcp_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(
        mcp_module,
        "create_session_factory",
        Mock(return_value=session_factory),
    )
    monkeypatch.setattr(mcp_module, "list_financial_facts", query)

    result = await mcp_module.run_mcp_fact_query(
        settings=MagicMock(),
        cik="0000320193",
        concepts=("Revenue",),
        limit=10,
    )

    assert result == [fact_record()]
    query.assert_awaited_once_with(
        session,
        cik="0000320193",
        concepts=("Revenue",),
        limit=10,
    )
    session.__aexit__.assert_awaited_once()
    engine.dispose.assert_awaited_once()


def test_mcp_main_runs_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The console entry point should select the interoperable stdio transport."""

    server = Mock()
    monkeypatch.setattr(mcp_module, "server", server)

    mcp_module.main()

    server.run.assert_called_once_with(transport="stdio")
