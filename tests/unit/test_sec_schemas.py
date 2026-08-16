"""Tests for typed SEC EDGAR submissions contracts."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from finsight.ingestion.sec_schemas import (
    SecCompanySubmissions,
    SecRecentFilings,
    normalize_cik,
)


def sample_submissions_payload() -> dict[str, Any]:
    """Return a representative SEC company-submissions response."""

    return {
        "cik": "320193",
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "sic": "3571",
        "fiscalYearEnd": "0927",
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0000320193-24-000123",
                    "0000320193-24-000124",
                ],
                "filingDate": ["2024-08-02", "2024-08-03"],
                "reportDate": ["2024-06-29", ""],
                "form": ["10-Q", "8-K"],
                "primaryDocument": [
                    "aapl-20240629.htm",
                    "aapl-20240802.htm",
                ],
            },
            "files": [
                {
                    "name": "CIK0000320193-submissions-001.json",
                    "filingCount": 2,
                    "filingFrom": "2020-01-01",
                    "filingTo": "2020-12-31",
                }
            ],
        },
    }


def test_normalize_cik_adds_required_leading_zeroes() -> None:
    """SEC CIKs should always use their ten-digit URL representation."""

    assert normalize_cik("320193") == "0000320193"
    assert normalize_cik(320193) == "0000320193"


@pytest.mark.parametrize("value", ["", "ABC123", "12345678901"])
def test_normalize_cik_rejects_invalid_values(value: str) -> None:
    """Malformed identifiers must be rejected before building SEC URLs."""

    with pytest.raises(ValueError):
        normalize_cik(value)


def test_company_submissions_parse_into_normalized_records() -> None:
    """The SEC column-oriented response should become typed filing records."""

    submissions = SecCompanySubmissions.model_validate(sample_submissions_payload())
    records = submissions.filings.recent.to_records()

    assert submissions.cik == "0000320193"
    assert submissions.name == "Apple Inc."
    assert submissions.primary_ticker == "AAPL"
    assert submissions.sic == "3571"
    assert submissions.fiscal_year_end == "0927"
    assert submissions.filings.files[0].filing_count == 2

    assert len(records) == 2
    assert records[0].accession_number == "0000320193-24-000123"
    assert records[0].filing_date == date(2024, 8, 2)
    assert records[0].report_date == date(2024, 6, 29)
    assert records[0].form_type == "10-Q"
    assert records[1].report_date is None


def test_company_submissions_normalize_blank_optional_codes() -> None:
    """Blank SEC codes and absent tickers should become explicit missing values."""

    payload = sample_submissions_payload()
    payload["tickers"] = []
    payload["sic"] = ""
    payload["fiscalYearEnd"] = ""

    submissions = SecCompanySubmissions.model_validate(payload)

    assert submissions.primary_ticker is None
    assert submissions.sic is None
    assert submissions.fiscal_year_end is None


def test_recent_filings_reject_misaligned_columns() -> None:
    """Parallel SEC filing arrays must describe the same number of rows."""

    with pytest.raises(ValidationError, match="misaligned"):
        SecRecentFilings.model_validate(
            {
                "accessionNumber": ["0000320193-24-000123"],
                "filingDate": [],
                "reportDate": [""],
                "form": ["10-Q"],
                "primaryDocument": ["aapl-20240629.htm"],
            }
        )


def test_company_submissions_reject_non_scalar_cik() -> None:
    """Structured values must not be accepted as SEC company identifiers."""

    payload = sample_submissions_payload()
    payload["cik"] = ["320193"]

    with pytest.raises(ValidationError, match="CIK must be a string or integer"):
        SecCompanySubmissions.model_validate(payload)
