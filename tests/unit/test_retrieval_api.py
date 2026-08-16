"""Tests for the citation-complete hybrid retrieval API."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import finsight.api.main as api_module
from finsight.api.main import create_app
from finsight.config.settings import Settings
from finsight.retrieval.search import (
    HybridSearchResult,
    RetrievalCitation,
    RetrievalQuery,
)


def search_result() -> HybridSearchResult:
    """Return one auditable fused result."""

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


def test_retrieval_endpoint_returns_scores_filters_and_citation() -> None:
    """The delivery contract should retain every auditable retrieval field."""

    handler = AsyncMock(return_value=[search_result()])
    application = create_app(
        Settings(environment="test"),
        retrieval_handler=handler,
    )

    with TestClient(application) as client:
        response = client.post(
            "/v1/retrieval/search",
            json={
                "query": "changed supply risks",
                "top_k": 5,
                "candidate_k": 20,
                "cik": "320193",
                "form_types": ["10-K"],
                "filed_from": "2025-01-01",
                "filed_to": "2025-12-31",
                "section_names": ["Item 1A. Risk Factors"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["results"][0]["matched_by"] == ["keyword", "semantic"]
    assert payload["results"][0]["citation"]["accession_number"] == ("0000320193-25-000079")
    assert handler.await_args is not None
    request = handler.await_args.args[0]
    assert request == RetrievalQuery(
        text="changed supply risks",
        top_k=5,
        candidate_k=20,
        cik="320193",
        form_types=("10-K",),
        filed_from=date(2025, 1, 1),
        filed_to=date(2025, 12, 31),
        section_names=("Item 1A. Risk Factors",),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "risk", "top_k": 10, "candidate_k": 5},
        {"query": "   "},
        {"query": "risk", "cik": "not-a-cik"},
        {"query": "risk", "form_types": ["10-k", "10-K"]},
        {"query": "risk", "section_names": ["Risk", "Risk"]},
        {
            "query": "risk",
            "filed_from": "2026-01-02",
            "filed_to": "2026-01-01",
        },
    ],
)
def test_retrieval_endpoint_rejects_contradictory_bounds(
    payload: dict[str, object],
) -> None:
    """Invalid public controls should receive typed validation errors."""

    application = create_app(Settings(environment="test"), retrieval_handler=AsyncMock())

    with TestClient(application) as client:
        response = client.post("/v1/retrieval/search", json=payload)

    assert response.status_code == 422


def test_retrieval_endpoint_reports_unconfigured_provider() -> None:
    """A missing production API key should produce a safe availability response."""

    application = create_app(Settings(environment="test"))

    with TestClient(application) as client:
        response = client.post(
            "/v1/retrieval/search",
            json={"query": "risk"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "retrieval embedding provider is not configured"


def test_retrieval_endpoint_does_not_mask_unexpected_value_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the known missing-provider condition should become a 503."""

    monkeypatch.setattr(
        api_module,
        "run_retrieval_query",
        AsyncMock(side_effect=ValueError("unexpected contract failure")),
    )
    application = create_app(Settings(environment="test"))

    with (
        TestClient(application) as client,
        pytest.raises(ValueError, match="unexpected contract"),
    ):
        client.post("/v1/retrieval/search", json={"query": "risk"})


@pytest.mark.asyncio
async def test_run_retrieval_query_releases_provider_and_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production query resources should close after a successful search."""

    settings = Mock()
    provider = MagicMock()
    provider.__aenter__ = AsyncMock(return_value=provider)
    provider.__aexit__ = AsyncMock(return_value=None)
    provider_factory = Mock()
    provider_factory.from_settings.return_value = provider
    engine = MagicMock()
    engine.dispose = AsyncMock()
    factory = Mock()
    search = AsyncMock(return_value=[search_result()])
    monkeypatch.setattr(api_module, "OpenAIEmbeddingProvider", provider_factory)
    monkeypatch.setattr(api_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(api_module, "create_session_factory", Mock(return_value=factory))
    monkeypatch.setattr(api_module, "hybrid_search", search)
    query = RetrievalQuery(text="risk")

    results = await api_module.run_retrieval_query(settings=settings, query=query)

    assert results == [search_result()]
    search.assert_awaited_once_with(
        query=query,
        provider=provider,
        session_factory=factory,
    )
    provider.__aexit__.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_retrieval_query_disposes_engine_after_search_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database resources should close when hybrid retrieval fails."""

    provider = MagicMock()
    provider.__aenter__ = AsyncMock(return_value=provider)
    provider.__aexit__ = AsyncMock(return_value=None)
    provider_factory = Mock()
    provider_factory.from_settings.return_value = provider
    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(api_module, "OpenAIEmbeddingProvider", provider_factory)
    monkeypatch.setattr(api_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(api_module, "create_session_factory", Mock(return_value=Mock()))
    monkeypatch.setattr(
        api_module,
        "hybrid_search",
        AsyncMock(side_effect=RuntimeError("search failed")),
    )

    with pytest.raises(RuntimeError, match="search failed"):
        await api_module.run_retrieval_query(
            settings=Mock(),
            query=RetrievalQuery(text="risk"),
        )

    engine.dispose.assert_awaited_once()
