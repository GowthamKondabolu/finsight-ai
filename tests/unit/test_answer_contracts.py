"""Tests for strict model-output and investigation request contracts."""

import pytest
from pydantic import ValidationError

from finsight.agents.contracts import (
    GeneratedCalculation,
    GeneratedClaim,
    GroundedAnswerDraft,
    InvestigationQuery,
)


def test_generated_contracts_normalize_valid_output() -> None:
    """Structured output should normalize text and evidence identifiers."""

    draft = GroundedAnswerDraft(
        claims=[GeneratedClaim(statement="  A supported claim. ", evidence_ids=["e1", " f1 "])],
        calculations=[
            GeneratedCalculation(
                statement=" Growth was ten percent. ",
                operation="percentage_change",
                fact_ids=["f1", "f2"],
                reported_value="10.0",
                reported_unit=" % ",
            )
        ],
        limitations=["  Historical filings only. "],
    )

    assert draft.claims[0].statement == "A supported claim."
    assert draft.claims[0].evidence_ids == ["E1", "F1"]
    assert draft.calculations[0].fact_ids == ["F1", "F2"]
    assert draft.calculations[0].reported_unit == "%"
    assert draft.limitations == ["Historical filings only."]


@pytest.mark.parametrize(
    "payload",
    [
        {"statement": " ", "evidence_ids": ["E1"]},
        {"statement": "claim", "evidence_ids": ["E1", "e1"]},
        {"statement": "claim", "evidence_ids": [" "]},
    ],
)
def test_generated_claim_rejects_ambiguous_output(payload: dict[str, object]) -> None:
    """Claims must contain useful text and unique citations."""

    with pytest.raises(ValidationError):
        GeneratedClaim.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"statement": " "},
        {"reported_unit": " "},
        {"fact_ids": ["E1"]},
        {"fact_ids": ["F1", "f1"]},
        {"reported_value": "not-a-number"},
        {"reported_value": "NaN"},
    ],
)
def test_generated_calculation_rejects_invalid_output(overrides: dict[str, object]) -> None:
    """Calculations must remain finite and restricted to fact IDs."""

    payload: dict[str, object] = {
        "statement": "calculation",
        "operation": "identity",
        "fact_ids": ["F1"],
        "reported_value": "1",
        "reported_unit": "USD",
    }
    payload.update(overrides)
    with pytest.raises(ValidationError):
        GeneratedCalculation.model_validate(payload)


@pytest.mark.parametrize("limitations", [[""], ["same", "same"]])
def test_draft_rejects_blank_or_duplicate_limitations(limitations: list[str]) -> None:
    """Limitations should be meaningful and non-repeated."""

    with pytest.raises(ValidationError):
        GroundedAnswerDraft(limitations=limitations)


def test_investigation_query_rejects_contradictory_payloads() -> None:
    """Requests must bound candidates, dates, and issuer-specific fact filters."""

    payloads = [
        {"question": "risk", "top_k": 5, "candidate_k": 4},
        {"question": "risk", "filed_from": "2026-02-01", "filed_to": "2026-01-01"},
        {"question": "risk", "fact_concepts": ["Revenue"]},
    ]
    for payload in payloads:
        with pytest.raises(ValidationError):
            InvestigationQuery.model_validate(payload)
