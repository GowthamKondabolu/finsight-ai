"""Persistence models for SEC filings and retrieval-ready document chunks."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
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

from finsight.storage.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIMENSIONS = 1536


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
