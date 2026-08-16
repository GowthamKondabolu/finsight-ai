"""Deterministic validation for model-proposed financial calculations."""

from __future__ import annotations

from decimal import Decimal

from finsight.agents.contracts import (
    FinancialFactEvidence,
    GeneratedCalculation,
    NumericalValidation,
)

ABSOLUTE_TOLERANCE = Decimal("0.01")
RELATIVE_TOLERANCE = Decimal("0.0001")


def _decimal_text(value: Decimal) -> str:
    """Serialize Decimal values without exponent notation."""

    return format(value, "f")


def _failure(
    calculation: GeneratedCalculation,
    message: str,
    *,
    expected_value: Decimal | None = None,
    expected_unit: str | None = None,
) -> NumericalValidation:
    """Build a failed validation without raising on model output."""

    return NumericalValidation(
        statement=calculation.statement,
        operation=calculation.operation,
        fact_ids=tuple(calculation.fact_ids),
        reported_value=calculation.reported_value,
        expected_value=_decimal_text(expected_value) if expected_value is not None else None,
        reported_unit=calculation.reported_unit,
        expected_unit=expected_unit,
        passed=False,
        message=message,
    )


def validate_calculation(
    calculation: GeneratedCalculation,
    facts: dict[str, FinancialFactEvidence],
) -> NumericalValidation:
    """Recompute supported arithmetic from exact Decimal SEC observations."""

    missing = [fact_id for fact_id in calculation.fact_ids if fact_id not in facts]
    if missing:
        return _failure(calculation, f"unknown financial fact identifiers: {', '.join(missing)}")

    operands = [facts[fact_id] for fact_id in calculation.fact_ids]
    values = [operand.value for operand in operands]
    operation = calculation.operation

    if operation == "identity":
        if len(values) != 1:
            return _failure(calculation, "identity requires exactly one financial fact")
        expected_value = values[0]
        expected_unit = operands[0].unit
    elif operation == "sum":
        if len(values) < 2:
            return _failure(calculation, "sum requires at least two financial facts")
        units = {operand.unit for operand in operands}
        if len(units) != 1:
            return _failure(calculation, "sum requires facts with the same unit")
        expected_value = sum(values, Decimal(0))
        expected_unit = operands[0].unit
    elif operation == "difference":
        if len(values) != 2:
            return _failure(calculation, "difference requires exactly two financial facts")
        if operands[0].unit != operands[1].unit:
            return _failure(calculation, "difference requires facts with the same unit")
        expected_value = values[0] - values[1]
        expected_unit = operands[0].unit
    elif operation == "ratio":
        if len(values) != 2:
            return _failure(calculation, "ratio requires exactly two financial facts")
        if operands[0].unit != operands[1].unit:
            return _failure(calculation, "ratio requires facts with the same unit")
        if values[1] == 0:
            return _failure(calculation, "ratio denominator cannot be zero")
        expected_value = values[0] / values[1]
        expected_unit = "ratio"
    else:
        if len(values) != 2:
            return _failure(
                calculation,
                "percentage_change requires previous and current financial facts",
            )
        if operands[0].unit != operands[1].unit:
            return _failure(calculation, "percentage_change requires facts with the same unit")
        if values[0] == 0:
            return _failure(calculation, "percentage_change baseline cannot be zero")
        expected_value = ((values[1] - values[0]) / abs(values[0])) * Decimal(100)
        expected_unit = "%"

    reported = Decimal(calculation.reported_value)
    tolerance = max(ABSOLUTE_TOLERANCE, abs(expected_value) * RELATIVE_TOLERANCE)
    value_matches = abs(reported - expected_value) <= tolerance
    unit_matches = calculation.reported_unit.casefold() == expected_unit.casefold()
    passed = value_matches and unit_matches
    message = (
        "validated against exact SEC facts"
        if passed
        else "reported arithmetic or unit does not match exact SEC facts"
    )
    return NumericalValidation(
        statement=calculation.statement,
        operation=operation,
        fact_ids=tuple(calculation.fact_ids),
        reported_value=calculation.reported_value,
        expected_value=_decimal_text(expected_value),
        reported_unit=calculation.reported_unit,
        expected_unit=expected_unit,
        passed=passed,
        message=message,
    )
