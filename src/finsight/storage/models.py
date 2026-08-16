"""Persistence models for SEC filings and retrieval-ready document chunks."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finsight.config.settings import DEFAULT_EMBEDDING_DIMENSIONS
from finsight.storage.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIMENSIONS = DEFAULT_EMBEDDING_DIMENSIONS


class Experiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable experiment plan with a separately controlled lifecycle state."""

    __tablename__ = "experiments"
    __table_args__ = (
        CheckConstraint(
            "plan_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_experiments_plan_fingerprint_format",
        ),
        CheckConstraint(
            "status IN ('draft', 'running', 'stopped', 'completed')",
            name="ck_experiments_status",
        ),
    )

    experiment_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    variants: Mapped[list[ExperimentVariant]] = relationship(
        back_populates="experiment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    assignments: Mapped[list[ExperimentAssignment]] = relationship(
        back_populates="experiment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ExperimentVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable control or treatment configuration."""

    __tablename__ = "experiment_variants"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "variant_key",
            name="uq_experiment_variants_experiment_key",
        ),
        CheckConstraint(
            "allocation_basis_points > 0 AND allocation_basis_points < 10000",
            name="ck_experiment_variants_allocation_bounds",
        ),
    )

    experiment_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_key: Mapped[str] = mapped_column(String(50), nullable=False)
    allocation_basis_points: Mapped[int] = mapped_column(Integer, nullable=False)
    is_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    experiment: Mapped[Experiment] = relationship(back_populates="variants")
    assignments: Mapped[list[ExperimentAssignment]] = relationship(back_populates="variant")


class ExperimentAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Sticky experiment assignment keyed only by an irreversible unit HMAC."""

    __tablename__ = "experiment_assignments"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "unit_hash",
            name="uq_experiment_assignments_experiment_unit",
        ),
        CheckConstraint(
            "unit_hash ~ '^[0-9a-f]{64}$'",
            name="ck_experiment_assignments_unit_hash_format",
        ),
    )

    experiment_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_variants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    unit_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    experiment: Mapped[Experiment] = relationship(back_populates="assignments")
    variant: Mapped[ExperimentVariant] = relationship(back_populates="assignments")
    events: Mapped[list[ExperimentEvent]] = relationship(
        back_populates="assignment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ExperimentEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only, idempotent exposure or outcome telemetry."""

    __tablename__ = "experiment_events"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id",
            "event_key",
            name="uq_experiment_events_assignment_event_key",
        ),
        CheckConstraint(
            "event_type IN ('exposure', 'outcome')",
            name="ck_experiment_events_type",
        ),
        CheckConstraint(
            "(event_type = 'exposure' AND metric_name IS NULL AND metric_value IS NULL) "
            "OR (event_type = 'outcome' AND metric_name IS NOT NULL "
            "AND metric_value IS NOT NULL)",
            name="ck_experiment_events_shape",
        ),
        Index(
            "uq_experiment_events_assignment_exposure",
            "assignment_id",
            unique=True,
            postgresql_where=text("event_type = 'exposure'"),
        ),
        Index(
            "uq_experiment_events_assignment_metric",
            "assignment_id",
            "metric_name",
            unique=True,
            postgresql_where=text("event_type = 'outcome'"),
        ),
    )

    assignment_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_assignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    metric_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    assignment: Mapped[ExperimentAssignment] = relationship(back_populates="events")


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A public company identified by its zero-padded SEC CIK."""

    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "cik ~ '^[0-9]{10}$'",
            name="ck_companies_cik_format",
        ),
    )

    cik: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        unique=True,
        index=True,
    )
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )
    sic: Mapped[str | None] = mapped_column(String(4), nullable=True)
    fiscal_year_end: Mapped[str | None] = mapped_column(
        String(4),
        nullable=True,
    )

    filings: Mapped[list[Filing]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    financial_facts: Mapped[list[FinancialFact]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FinancialFact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One normalized, period-specific SEC XBRL company fact."""

    __tablename__ = "financial_facts"
    __table_args__ = (
        CheckConstraint(
            "observation_key ~ '^[0-9a-f]{64}$'",
            name="ck_financial_facts_observation_key_format",
        ),
        Index(
            "ix_financial_facts_company_concept_period",
            "company_id",
            "taxonomy",
            "concept",
            "end_date",
        ),
    )

    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observation_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    taxonomy: Mapped[str] = mapped_column(String(50), nullable=False)
    concept: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    filed_date: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(10), nullable=True)
    form_type: Mapped[str] = mapped_column(String(20), nullable=False)
    accession_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    frame: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    company: Mapped[Company] = relationship(back_populates="financial_facts")


class Filing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An immutable SEC filing discovered from EDGAR."""

    __tablename__ = "filings"

    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    accession_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
    )
    form_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    filing_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    primary_document: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    company: Mapped[Company] = relationship(back_populates="filings")
    sections: Mapped[list[FilingSection]] = relationship(
        back_populates="filing",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FilingSection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An ordered, source-preserving section extracted from a filing."""

    __tablename__ = "filing_sections"
    __table_args__ = (
        UniqueConstraint(
            "filing_id",
            "sequence_number",
            name="uq_filing_sections_filing_sequence",
        ),
        CheckConstraint(
            "sequence_number >= 0",
            name="ck_filing_sections_sequence_nonnegative",
        ),
        CheckConstraint(
            "char_count >= 0",
            name="ck_filing_sections_char_count_nonnegative",
        ),
    )

    filing_id: Mapped[UUID] = mapped_column(
        ForeignKey("filings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    filing: Mapped[Filing] = relationship(back_populates="sections")
    chunks: Mapped[list[FilingChunk]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FilingChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A retrieval unit with keyword and vector-search representations."""

    __tablename__ = "filing_chunks"
    __table_args__ = (
        UniqueConstraint(
            "section_id",
            "chunk_index",
            name="uq_filing_chunks_section_index",
        ),
        CheckConstraint(
            "chunk_index >= 0",
            name="ck_filing_chunks_index_nonnegative",
        ),
        CheckConstraint(
            "token_count >= 0",
            name="ck_filing_chunks_token_count_nonnegative",
        ),
        Index(
            "ix_filing_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_filing_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    section_id: Mapped[UUID] = mapped_column(
        ForeignKey("filing_sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(content, ''))",
            persisted=True,
        ),
        nullable=False,
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(EMBEDDING_DIMENSIONS),
        nullable=True,
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    section: Mapped[FilingSection] = relationship(back_populates="chunks")
