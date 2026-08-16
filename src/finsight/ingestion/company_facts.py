"""Typed SEC company-facts contracts and normalized financial observations."""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from finsight.ingestion.sec_schemas import normalize_cik

MAX_COMPANY_FACT_OBSERVATIONS = 100_000


class NormalizedCompanyFact(BaseModel):
    """One issuer fact with a deterministic cross-run identity."""

    model_config = ConfigDict(frozen=True)

    observation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    taxonomy: str
    concept: str
    label: str
    description: str
    unit: str
    value: Decimal
    start_date: date | None
    end_date: date
    filed_date: date
    fiscal_year: int | None
    fiscal_period: str | None
    form_type: str
    accession_number: str
    frame: str | None


class SecFactObservation(BaseModel):
    """One period-specific observation inside an SEC company-facts unit."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    value: Decimal = Field(alias="val")
    accession_number: str = Field(alias="accn", pattern=r"^\d{10}-\d{2}-\d{6}$")
    fiscal_year: int | None = Field(default=None, alias="fy")
    fiscal_period: str | None = Field(default=None, alias="fp", max_length=10)
    form_type: str = Field(alias="form", min_length=1, max_length=20)
    filed_date: date = Field(alias="filed")
    start_date: date | None = Field(default=None, alias="start")
    end_date: date = Field(alias="end")
    frame: str | None = Field(default=None, max_length=50)


class SecFactDefinition(BaseModel):
    """A labeled XBRL concept with observations grouped by unit."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    label: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10_000)
    units: dict[str, list[SecFactObservation]] = Field(default_factory=dict)


class SecCompanyFacts(BaseModel):
    """Validated SEC company-facts response for one public company."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    cik: str
    entity_name: str = Field(alias="entityName", min_length=1, max_length=255)
    facts: dict[str, dict[str, SecFactDefinition]] = Field(default_factory=dict)

    @field_validator("cik", mode="before")
    @classmethod
    def validate_cik(cls, value: object) -> str:
        """Normalize CIKs received as strings or integers."""

        if not isinstance(value, (str, int)):
            raise ValueError("CIK must be a string or integer")

        return normalize_cik(value)

    @property
    def observation_count(self) -> int:
        """Return the number of source observations across all taxonomies."""

        return sum(
            len(observations)
            for concepts in self.facts.values()
            for definition in concepts.values()
            for observations in definition.units.values()
        )

    def to_records(
        self,
        taxonomies: Collection[str] | None = None,
    ) -> tuple[NormalizedCompanyFact, ...]:
        """Flatten selected SEC taxonomies into deterministic typed records."""

        selected_taxonomies = (
            frozenset(taxonomies) if taxonomies is not None else frozenset(self.facts)
        )
        records: list[NormalizedCompanyFact] = []

        for taxonomy in sorted(selected_taxonomies):
            concepts = self.facts.get(taxonomy, {})

            for concept in sorted(concepts):
                definition = concepts[concept]

                for unit in sorted(definition.units):
                    for observation in definition.units[unit]:
                        records.append(
                            NormalizedCompanyFact(
                                observation_key=self._observation_key(
                                    taxonomy,
                                    concept,
                                    unit,
                                    observation,
                                ),
                                taxonomy=taxonomy,
                                concept=concept,
                                label=definition.label,
                                description=definition.description,
                                unit=unit,
                                value=observation.value,
                                start_date=observation.start_date,
                                end_date=observation.end_date,
                                filed_date=observation.filed_date,
                                fiscal_year=observation.fiscal_year,
                                fiscal_period=observation.fiscal_period,
                                form_type=observation.form_type,
                                accession_number=observation.accession_number,
                                frame=observation.frame,
                            )
                        )

                        if len(records) > MAX_COMPANY_FACT_OBSERVATIONS:
                            raise ValueError(
                                "SEC company-facts response exceeds the "
                                "observation processing limit"
                            )

        return tuple(records)

    @staticmethod
    def _observation_key(
        taxonomy: str,
        concept: str,
        unit: str,
        observation: SecFactObservation,
    ) -> str:
        """Hash the immutable source identity of one SEC fact observation."""

        identity = "|".join(
            (
                taxonomy,
                concept,
                unit,
                observation.accession_number,
                observation.start_date.isoformat() if observation.start_date else "",
                observation.end_date.isoformat(),
                observation.filed_date.isoformat(),
                str(observation.fiscal_year) if observation.fiscal_year is not None else "",
                observation.fiscal_period or "",
                observation.form_type,
                observation.frame or "",
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()
