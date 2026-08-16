"""Tests for SEC company-facts validation and normalization."""

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

import finsight.ingestion.company_facts as facts_module
from finsight.ingestion.company_facts import SecCompanyFacts


def sample_company_facts_payload() -> dict[str, Any]:
    """Return representative instant and duration XBRL facts."""

    return {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "label": "Entity Common Stock, Shares Outstanding",
                    "description": "Shares outstanding at the measurement date.",
                    "units": {
                        "shares": [
                            {
                                "end": "2024-08-02",
                                "val": 15204137000,
                                "accn": "0000320193-24-000123",
                                "fy": 2024,
                                "fp": "Q3",
                                "form": "10-Q",
                                "filed": "2024-08-02",
                                "frame": "CY2024Q2I",
                            }
                        ]
                    },
                }
            },
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "description": "Revenue from customers.",
                    "units": {
                        "USD": [
                            {
                                "start": "2024-04-01",
                                "end": "2024-06-29",
                                "val": "85777000000",
                                "accn": "0000320193-24-000123",
                                "fy": 2024,
                                "fp": "Q3",
                                "form": "10-Q",
                                "filed": "2024-08-02",
                                "frame": "CY2024Q2",
                            }
                        ]
                    },
                }
            },
        },
    }


def test_company_facts_normalize_selected_taxonomies() -> None:
    """Nested SEC facts should become stable typed observations."""

    payload = SecCompanyFacts.model_validate(sample_company_facts_payload())
    records = payload.to_records({"us-gaap"})

    assert payload.cik == "0000320193"
    assert payload.entity_name == "Apple Inc."
    assert payload.observation_count == 2
    assert len(records) == 1

    revenue = records[0]
    assert revenue.taxonomy == "us-gaap"
    assert revenue.concept == "Revenues"
    assert revenue.value == Decimal("85777000000")
    assert revenue.start_date == date(2024, 4, 1)
    assert revenue.end_date == date(2024, 6, 29)
    assert revenue.filed_date == date(2024, 8, 2)
    assert revenue.unit == "USD"
    assert revenue.observation_key == SecCompanyFacts._observation_key(
        "us-gaap",
        "Revenues",
        "USD",
        payload.facts["us-gaap"]["Revenues"].units["USD"][0],
    )
    assert len(revenue.observation_key) == hashlib.sha256().digest_size * 2


def test_company_facts_default_to_every_available_taxonomy() -> None:
    """Omitting filters should retain all validated SEC observations."""

    payload = SecCompanyFacts.model_validate(sample_company_facts_payload())

    records = payload.to_records()

    assert [record.taxonomy for record in records] == ["dei", "us-gaap"]
    assert records[0].start_date is None
    assert records[0].value == Decimal("15204137000")


def test_company_facts_ignore_unknown_selected_taxonomies() -> None:
    """A missing optional taxonomy should produce no synthetic records."""

    payload = SecCompanyFacts.model_validate(sample_company_facts_payload())

    assert payload.to_records({"invest"}) == ()


def test_company_facts_reject_non_scalar_cik() -> None:
    """Structured CIK values must fail before URL or persistence use."""

    payload = sample_company_facts_payload()
    payload["cik"] = ["320193"]

    with pytest.raises(ValidationError, match="CIK must be a string or integer"):
        SecCompanyFacts.model_validate(payload)


def test_company_facts_reject_non_numeric_values() -> None:
    """Financial observations must remain exact numeric values."""

    payload = sample_company_facts_payload()
    payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]["val"] = "unknown"

    with pytest.raises(ValidationError):
        SecCompanyFacts.model_validate(payload)


def test_company_facts_enforce_observation_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pathological company-facts payloads should fail within a hard bound."""

    monkeypatch.setattr(facts_module, "MAX_COMPANY_FACT_OBSERVATIONS", 1)
    payload = SecCompanyFacts.model_validate(sample_company_facts_payload())

    with pytest.raises(ValueError, match="observation processing limit"):
        payload.to_records()
