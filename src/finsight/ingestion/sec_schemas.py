"""Typed contracts for SEC EDGAR company-submissions responses."""

from __future__ import annotations

from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_cik(value: str | int) -> str:
    """Return a validated ten-digit SEC Central Index Key."""

    candidate = str(value).strip()

    if not candidate.isascii() or not candidate.isdigit():
        raise ValueError("CIK must contain only ASCII digits")

    if len(candidate) > 10:
        raise ValueError("CIK cannot exceed 10 digits")

    return candidate.zfill(10)


class SecFilingMetadata(BaseModel):
    """One normalized filing record from the SEC submissions response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accession_number: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    filing_date: date
    report_date: date | None = None
    form_type: str = Field(min_length=1, max_length=20)
    primary_document: str = Field(min_length=1, max_length=255)


class SecRecentFilings(BaseModel):
    """Column-oriented recent-filings payload returned by the SEC."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    accession_numbers: list[str] = Field(
        default_factory=list,
        alias="accessionNumber",
    )
    filing_dates: list[date] = Field(
        default_factory=list,
        alias="filingDate",
    )
    report_dates: list[str] = Field(
        default_factory=list,
        alias="reportDate",
    )
    forms: list[str] = Field(default_factory=list, alias="form")
    primary_documents: list[str] = Field(
        default_factory=list,
        alias="primaryDocument",
    )

    @model_validator(mode="after")
    def validate_aligned_columns(self) -> Self:
        """Reject responses whose required filing columns are misaligned."""

        expected_rows = len(self.accession_numbers)
        column_lengths = {
            "filingDate": len(self.filing_dates),
            "reportDate": len(self.report_dates),
            "form": len(self.forms),
            "primaryDocument": len(self.primary_documents),
        }
        mismatched = [
            name for name, row_count in column_lengths.items() if row_count != expected_rows
        ]

        if mismatched:
            names = ", ".join(sorted(mismatched))
            raise ValueError(f"SEC recent filing arrays are misaligned: {names}")

        return self

    def to_records(self) -> list[SecFilingMetadata]:
        """Convert SEC column arrays into normalized filing records."""

        records: list[SecFilingMetadata] = []

        for index, accession_number in enumerate(self.accession_numbers):
            report_date_value = self.report_dates[index]
            report_date = date.fromisoformat(report_date_value) if report_date_value else None
            records.append(
                SecFilingMetadata(
                    accession_number=accession_number,
                    filing_date=self.filing_dates[index],
                    report_date=report_date,
                    form_type=self.forms[index],
                    primary_document=self.primary_documents[index],
                )
            )

        return records


class SecSubmissionHistoryFile(BaseModel):
    """Metadata for an additional historical submissions JSON file."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str = Field(min_length=1)
    filing_count: int = Field(alias="filingCount", ge=0)
    filing_from: date = Field(alias="filingFrom")
    filing_to: date = Field(alias="filingTo")


class SecFilingsPayload(BaseModel):
    """Recent and historical filing references for a company."""

    model_config = ConfigDict(extra="ignore")

    recent: SecRecentFilings
    files: list[SecSubmissionHistoryFile] = Field(default_factory=list)


class SecCompanySubmissions(BaseModel):
    """Validated company-submissions response from data.sec.gov."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    cik: str
    name: str = Field(min_length=1, max_length=255)
    tickers: list[str] = Field(default_factory=list)
    exchanges: list[str] = Field(default_factory=list)
    sic: str | None = None
    fiscal_year_end: str | None = Field(default=None, alias="fiscalYearEnd")
    filings: SecFilingsPayload

    @field_validator("cik", mode="before")
    @classmethod
    def validate_cik(cls, value: object) -> str:
        """Normalize CIKs received as either strings or integers."""

        if not isinstance(value, (str, int)):
            raise ValueError("CIK must be a string or integer")

        return normalize_cik(value)

    @field_validator("sic", "fiscal_year_end", mode="before")
    @classmethod
    def normalize_optional_codes(cls, value: object) -> object:
        """Represent blank SEC codes as missing values."""

        return None if value == "" else value

    @property
    def primary_ticker(self) -> str | None:
        """Return the first ticker when the SEC provides one."""

        return self.tickers[0] if self.tickers else None
