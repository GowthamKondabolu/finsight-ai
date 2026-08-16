"""Tests for the grounded investigation answer API."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi.testclient import TestClient

import finsight.api.main as api_module
from finsight.agents.contracts import (
    AnswerSource,
    GroundedAnswerResult,
    InvestigationQuery,
    NumericalValidation,
    ValidatedClaim,
)
from finsight.agents.generation import AnswerGenerationError
from finsight.agents.investigation import GroundedAnswerContractError
from finsight.api.main import create_app
from finsight.config.settings import Settings


def answer_result() -> GroundedAnswerResult:
    """Return one complete API result with passage and fact provenance."""

    return GroundedAnswerResult(
        question="What changed?",
        status="grounded",
        answer="Supply risk increased. [E1]",
        claims=(ValidatedClaim(statement="Supply risk increased.", citation_ids=("E1",)),),
        numerical_validations=(
            NumericalValidation(
                statement="Revenue increased.",
                operation="percentage_change",
                fact_ids=("F2", "F1"),
                reported_value="25",
                expected_value="25",
                reported_unit="%",
                expected_unit="%",
                passed=True,
                message="validated against exact SEC facts",
            ),
        ),
        sources=(
            AnswerSource(
                source_id="E1",
                source_type="filing_passage",
                label="10-K — Risk Factors",
                source_url="https://www.sec.gov/example",
                accession_number="0000320193-25-000079",
                form_type="10-K",
                filing_date=date(2025, 10, 31),
                section_name="Risk Factors",
                chunk_index=0,
                content_hash="a" * 64,
            ),
        ),
        limitations=(),
        model_name="generation-model",
        requires_human_review=True,
        review_reasons=("financial analysis requires qualified human review",),
    )


def test_investigation_endpoint_returns_grounding_and_validation_state() -> None:
    """The API should preserve citations, checks, and mandatory review state."""

    handler = AsyncMock(return_value=answer_result())
    application = create_app(Settings(environment="test"), investigation_handler=handler)

    with TestClient(application) as client:
        response = client.post(
            "/v1/investigations/answer",
            json={
                "question": "  What changed?  ",
                "top_k": 5,
                "candidate_k": 20,
                "cik": "320193",
                "form_types": ["10-k"],
                "fact_concepts": ["Revenue"],
                "fact_limit": 10,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "grounded"
    assert payload["claims"][0]["citation_ids"] == ["E1"]
    assert payload["numerical_validations"][0]["passed"] is True
    assert payload["requires_human_review"] is True
    assert handler.await_args is not None
    assert handler.await_args.args[0] == InvestigationQuery(
        question="What changed?",
        top_k=5,
        candidate_k=20,
        cik="320193",
        form_types=("10-K",),
        fact_concepts=("Revenue",),
        fact_limit=10,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"question": " "},
        {"question": "risk", "top_k": 10, "candidate_k": 5},
        {"question": "risk", "cik": "bad"},
        {"question": "risk", "form_types": ["10-k", "10-K"]},
        {"question": "risk", "section_names": ["Risk", "Risk"]},
        {"question": "risk", "fact_concepts": ["Revenue"]},
        {
            "question": "risk",
            "filed_from": "2026-02-01",
            "filed_to": "2026-01-01",
        },
    ],
)
def test_investigation_endpoint_rejects_invalid_controls(payload: dict[str, object]) -> None:
    """Contradictory or ambiguous public controls should return HTTP 422."""

    application = create_app(
        Settings(environment="test"),
        investigation_handler=AsyncMock(),
    )
    with TestClient(application) as client:
        response = client.post("/v1/investigations/answer", json=payload)
    assert response.status_code == 422


def test_investigation_endpoint_reports_unconfigured_ai_providers() -> None:
    """A missing API key should produce a safe service-availability response."""

    application = create_app(Settings(environment="test"))
    with TestClient(application) as client:
        response = client.post("/v1/investigations/answer", json={"question": "risk?"})
    assert response.status_code == 503
    assert response.json()["detail"] == "investigation AI providers are not configured"


@pytest.mark.parametrize(
    "error",
    [
        AnswerGenerationError("no output"),
        GroundedAnswerContractError("invented citation"),
    ],
)
def test_investigation_endpoint_masks_generation_contract_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: RuntimeError,
) -> None:
    """Provider refusals and invented citations should become a safe HTTP 502."""

    monkeypatch.setattr(api_module, "run_investigation_query", AsyncMock(side_effect=error))
    application = create_app(Settings(environment="test"))
    with TestClient(application) as client:
        response = client.post("/v1/investigations/answer", json={"question": "risk?"})
    assert response.status_code == 502
    assert response.json()["detail"] == "generated answer failed the grounding contract"


def test_investigation_endpoint_does_not_mask_unexpected_value_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only known missing-provider configuration should become HTTP 503."""

    monkeypatch.setattr(
        api_module,
        "run_investigation_query",
        AsyncMock(side_effect=ValueError("unexpected failure")),
    )
    application = create_app(Settings(environment="test"))
    with TestClient(application) as client, pytest.raises(ValueError, match="unexpected"):
        client.post("/v1/investigations/answer", json={"question": "risk?"})


@pytest.mark.asyncio
async def test_run_investigation_query_releases_all_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production providers and database engines should close after success."""

    embedding = MagicMock()
    embedding.__aenter__ = AsyncMock(return_value=embedding)
    embedding.__aexit__ = AsyncMock(return_value=None)
    embedding_factory = Mock()
    embedding_factory.from_settings.return_value = embedding
    generator = MagicMock()
    generator.__aenter__ = AsyncMock(return_value=generator)
    generator.__aexit__ = AsyncMock(return_value=None)
    generator_factory = Mock()
    generator_factory.from_settings.return_value = generator
    engine = MagicMock()
    engine.dispose = AsyncMock()
    factory = Mock()
    service = AsyncMock(return_value=answer_result())
    monkeypatch.setattr(api_module, "OpenAIEmbeddingProvider", embedding_factory)
    monkeypatch.setattr(api_module, "OpenAIAnswerGenerator", generator_factory)
    monkeypatch.setattr(api_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(api_module, "create_session_factory", Mock(return_value=factory))
    monkeypatch.setattr(api_module, "answer_investigation", service)
    query = InvestigationQuery(question="risk?")

    result = await api_module.run_investigation_query(settings=Mock(), query=query)

    assert result == answer_result()
    service.assert_awaited_once_with(
        query=query,
        embedding_provider=embedding,
        answer_generator=generator,
        session_factory=factory,
    )
    embedding.__aexit__.assert_awaited_once()
    generator.__aexit__.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_investigation_query_disposes_engine_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database cleanup should run when the investigation service fails."""

    embedding = MagicMock()
    embedding.__aenter__ = AsyncMock(return_value=embedding)
    embedding.__aexit__ = AsyncMock(return_value=None)
    embedding_factory = Mock()
    embedding_factory.from_settings.return_value = embedding
    generator = MagicMock()
    generator.__aenter__ = AsyncMock(return_value=generator)
    generator.__aexit__ = AsyncMock(return_value=None)
    generator_factory = Mock()
    generator_factory.from_settings.return_value = generator
    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(api_module, "OpenAIEmbeddingProvider", embedding_factory)
    monkeypatch.setattr(api_module, "OpenAIAnswerGenerator", generator_factory)
    monkeypatch.setattr(api_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(api_module, "create_session_factory", Mock(return_value=Mock()))
    monkeypatch.setattr(
        api_module,
        "answer_investigation",
        AsyncMock(side_effect=RuntimeError("failed")),
    )

    with pytest.raises(RuntimeError, match="failed"):
        await api_module.run_investigation_query(
            settings=Mock(),
            query=InvestigationQuery(question="risk?"),
        )
    engine.dispose.assert_awaited_once()
