"""Tests for deterministic SEC-fact arithmetic validation."""

from datetime import date
from decimal import Decimal

import pytest

from finsight.agents.contracts import FinancialFactEvidence, GeneratedCalculation
from finsight.guardrails.numerical import validate_calculation


def fact(source_id: str, value: str, unit: str = "USD") -> FinancialFactEvidence:
    """Return an exact financial fact for guardrail tests."""

    return FinancialFactEvidence(
        source_id=source_id,
        observation_key=source_id.lower() * 32,
        concept=f"Concept{source_id}",
        label=f"Fact {source_id}",
        unit=unit,
        value=Decimal(value),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        filed_date=date(2026, 2, 1),
        fiscal_year=2025,
        fiscal_period="FY",
        form_type="10-K",
        accession_number="0000000001-26-000001",
        source_url="https://data.sec.gov/example",
    )


def calculation(
    operation: str,
    fact_ids: list[str],
    value: str,
    unit: str,
) -> GeneratedCalculation:
    """Build a schema-valid calculation for deterministic validation."""

    return GeneratedCalculation.model_validate(
        {
            "statement": "Calculated statement.",
            "operation": operation,
            "fact_ids": fact_ids,
            "reported_value": value,
            "reported_unit": unit,
        }
    )


@pytest.mark.parametrize(
    ("operation", "fact_ids", "value", "unit", "expected"),
    [
        ("identity", ["F1"], "100", "USD", "100"),
        ("sum", ["F1", "F2"], "140", "USD", "140"),
        ("difference", ["F1", "F2"], "60", "USD", "60"),
        ("ratio", ["F1", "F2"], "2.5", "ratio", "2.5"),
        ("percentage_change", ["F2", "F1"], "150", "%", "150.0"),
    ],
)
def test_supported_calculations_pass_exact_recomputation(
    operation: str,
    fact_ids: list[str],
    value: str,
    unit: str,
    expected: str,
) -> None:
    """Every supported operation should be recomputed from Decimal facts."""

    result = validate_calculation(
        calculation(operation, fact_ids, value, unit),
        {"F1": fact("F1", "100"), "F2": fact("F2", "40")},
    )

    assert result.passed is True
    assert result.expected_value == expected
    assert result.message == "validated against exact SEC facts"


@pytest.mark.parametrize(
    ("calc", "facts", "message"),
    [
        (calculation("identity", ["F9"], "1", "USD"), {}, "unknown"),
        (calculation("identity", ["F1", "F2"], "1", "USD"), {}, "unknown"),
        (
            calculation("identity", ["F1", "F2"], "1", "USD"),
            {"F1": fact("F1", "1"), "F2": fact("F2", "2")},
            "exactly one",
        ),
        (
            calculation("sum", ["F1"], "1", "USD"),
            {"F1": fact("F1", "1")},
            "at least two",
        ),
        (
            calculation("sum", ["F1", "F2"], "3", "USD"),
            {"F1": fact("F1", "1"), "F2": fact("F2", "2", "shares")},
            "same unit",
        ),
        (
            calculation("difference", ["F1"], "1", "USD"),
            {"F1": fact("F1", "1")},
            "exactly two",
        ),
        (
            calculation("difference", ["F1", "F2"], "1", "USD"),
            {"F1": fact("F1", "2"), "F2": fact("F2", "1", "shares")},
            "same unit",
        ),
        (
            calculation("ratio", ["F1"], "1", "ratio"),
            {"F1": fact("F1", "1")},
            "exactly two",
        ),
        (
            calculation("ratio", ["F1", "F2"], "1", "ratio"),
            {"F1": fact("F1", "1"), "F2": fact("F2", "2", "shares")},
            "same unit",
        ),
        (
            calculation("ratio", ["F1", "F2"], "1", "ratio"),
            {"F1": fact("F1", "1"), "F2": fact("F2", "0")},
            "denominator",
        ),
        (
            calculation("percentage_change", ["F1"], "1", "%"),
            {"F1": fact("F1", "1")},
            "previous and current",
        ),
        (
            calculation("percentage_change", ["F1", "F2"], "1", "%"),
            {"F1": fact("F1", "1"), "F2": fact("F2", "2", "shares")},
            "same unit",
        ),
        (
            calculation("percentage_change", ["F1", "F2"], "1", "%"),
            {"F1": fact("F1", "0"), "F2": fact("F2", "2")},
            "baseline",
        ),
    ],
)
def test_invalid_calculations_fail_safely(
    calc: GeneratedCalculation,
    facts: dict[str, FinancialFactEvidence],
    message: str,
) -> None:
    """Bad references, arity, units, and denominators should not raise."""

    result = validate_calculation(calc, facts)

    assert result.passed is False
    assert message in result.message


def test_mismatched_value_or_unit_is_reported_with_expected_result() -> None:
    """Model arithmetic outside tolerance must be visible and excluded later."""

    wrong_value = validate_calculation(
        calculation("difference", ["F1", "F2"], "90", "USD"),
        {"F1": fact("F1", "100"), "F2": fact("F2", "40")},
    )
    wrong_unit = validate_calculation(
        calculation("identity", ["F1"], "100", "shares"),
        {"F1": fact("F1", "100")},
    )

    assert wrong_value.passed is False
    assert wrong_value.expected_value == "60"
    assert wrong_value.expected_unit == "USD"
    assert wrong_unit.passed is False
