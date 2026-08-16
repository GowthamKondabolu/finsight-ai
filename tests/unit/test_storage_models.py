"""Tests for the SEC filing persistence schema."""

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import CheckConstraint, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from finsight.storage.base import NAMING_CONVENTION, Base
from finsight.storage.models import EMBEDDING_DIMENSIONS


def test_metadata_registers_expected_tables_and_shared_columns() -> None:
    """Every domain table should use the shared identity and audit contract."""

    expected_tables = {
        "companies",
        "experiment_assignments",
        "experiment_events",
        "experiment_variants",
        "experiments",
        "investigation_feedback",
        "financial_facts",
        "filings",
        "filing_sections",
        "filing_chunks",
    }

    assert set(Base.metadata.tables) == expected_tables
    assert Base.metadata.naming_convention == NAMING_CONVENTION

    for table_name in expected_tables:
        columns = Base.metadata.tables[table_name].c
        assert "id" in columns
        assert "created_at" in columns
        assert "updated_at" in columns


def test_company_and_filing_constraints_preserve_source_identity() -> None:
    """CIKs and accession numbers should be unique and source relationships cascading."""

    companies = Base.metadata.tables["companies"]
    filings = Base.metadata.tables["filings"]

    assert companies.c.cik.unique is True
    assert filings.c.accession_number.unique is True

    company_checks = {
        constraint.name
        for constraint in companies.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_companies_cik_format" in company_checks

    company_foreign_key = next(iter(filings.c.company_id.foreign_keys))
    assert company_foreign_key.target_fullname == "companies.id"
    assert company_foreign_key.ondelete == "CASCADE"


def test_financial_facts_preserve_numeric_precision_and_source_identity() -> None:
    """Normalized XBRL facts should be exact, unique, and issuer-scoped."""

    facts = Base.metadata.tables["financial_facts"]
    fact_checks = {
        constraint.name
        for constraint in facts.constraints
        if isinstance(constraint, CheckConstraint)
    }
    value_type = facts.c.value.type

    assert facts.c.observation_key.unique is True
    assert isinstance(value_type, Numeric)
    assert value_type.asdecimal is True
    assert value_type.precision is None
    assert value_type.scale is None
    assert isinstance(facts.c.source_metadata.type, JSONB)
    assert "ck_financial_facts_observation_key_format" in fact_checks

    company_foreign_key = next(iter(facts.c.company_id.foreign_keys))
    assert company_foreign_key.target_fullname == "companies.id"
    assert company_foreign_key.ondelete == "CASCADE"

    concept_index = next(
        index
        for index in facts.indexes
        if index.name == "ix_financial_facts_company_concept_period"
    )
    assert [column.name for column in concept_index.columns] == [
        "company_id",
        "taxonomy",
        "concept",
        "end_date",
    ]


def test_sections_and_chunks_enforce_ordered_nonnegative_content() -> None:
    """Sections and chunks should be ordered uniquely within their parents."""

    sections = Base.metadata.tables["filing_sections"]
    chunks = Base.metadata.tables["filing_chunks"]

    section_unique_constraints = {
        constraint.name
        for constraint in sections.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    chunk_unique_constraints = {
        constraint.name
        for constraint in chunks.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert "uq_filing_sections_filing_sequence" in section_unique_constraints
    assert "uq_filing_chunks_section_index" in chunk_unique_constraints

    section_foreign_key = next(iter(sections.c.filing_id.foreign_keys))
    chunk_foreign_key = next(iter(chunks.c.section_id.foreign_keys))

    assert section_foreign_key.ondelete == "CASCADE"
    assert chunk_foreign_key.ondelete == "CASCADE"
    assert isinstance(sections.c.source_metadata.type, JSONB)


def test_chunk_schema_supports_keyword_and_vector_retrieval() -> None:
    """The retrieval table should expose generated text search and HNSW vector search."""

    chunks = Base.metadata.tables["filing_chunks"]
    embedding_type = chunks.c.embedding.type

    assert isinstance(embedding_type, VECTOR)
    assert embedding_type.dim == EMBEDDING_DIMENSIONS

    computed_search_vector = chunks.c.search_vector.computed
    assert computed_search_vector is not None
    assert "to_tsvector" in str(computed_search_vector.sqltext)

    search_index = next(
        index for index in chunks.indexes if index.name == "ix_filing_chunks_search_vector"
    )
    vector_index = next(
        index for index in chunks.indexes if index.name == "ix_filing_chunks_embedding_hnsw"
    )

    search_options = search_index.dialect_options["postgresql"]
    vector_options = vector_index.dialect_options["postgresql"]

    assert search_options["using"] == "gin"
    assert vector_options["using"] == "hnsw"
    assert vector_options["ops"] == {"embedding": "vector_cosine_ops"}


def test_experiment_schema_preserves_plan_assignment_and_event_identity() -> None:
    """Experiment plans, sticky assignments, and telemetry need database enforcement."""

    experiments = Base.metadata.tables["experiments"]
    variants = Base.metadata.tables["experiment_variants"]
    assignments = Base.metadata.tables["experiment_assignments"]
    events = Base.metadata.tables["experiment_events"]

    experiment_checks = {
        constraint.name
        for constraint in experiments.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assignment_uniques = {
        constraint.name
        for constraint in assignments.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    event_indexes = {index.name for index in events.indexes}

    assert experiments.c.experiment_key.unique is True
    assert experiments.c.plan_fingerprint.unique is True
    assert isinstance(experiments.c.plan.type, JSONB)
    assert "ck_experiments_status" in experiment_checks
    assert "uq_experiment_assignments_experiment_unit" in assignment_uniques
    assert "uq_experiment_events_assignment_exposure" in event_indexes
    assert "uq_experiment_events_assignment_metric" in event_indexes
    assert isinstance(events.c.metric_value.type, Numeric)

    variant_foreign_key = next(iter(variants.c.experiment_id.foreign_keys))
    assignment_foreign_key = next(iter(assignments.c.experiment_id.foreign_keys))
    event_foreign_key = next(iter(events.c.assignment_id.foreign_keys))
    assert variant_foreign_key.ondelete == "CASCADE"
    assert assignment_foreign_key.ondelete == "CASCADE"
    assert event_foreign_key.ondelete == "CASCADE"


def test_feedback_schema_bounds_quality_and_idempotency() -> None:
    """Analyst feedback should be bounded and idempotent per workflow thread."""

    feedback = Base.metadata.tables["investigation_feedback"]
    checks = {
        constraint.name
        for constraint in feedback.constraints
        if isinstance(constraint, CheckConstraint)
    }
    uniques = {
        constraint.name
        for constraint in feedback.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert "ck_investigation_feedback_rating" in checks
    assert "ck_investigation_feedback_evidence_quality" in checks
    assert "uq_investigation_feedback_thread_key" in uniques
    assert isinstance(feedback.c.tags.type, JSONB)
