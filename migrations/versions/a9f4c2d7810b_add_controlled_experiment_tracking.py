"""add controlled experiment tracking

Revision ID: a9f4c2d7810b
Revises: e4d8a6c2f190
Create Date: 2026-08-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9f4c2d7810b"
down_revision: str | None = "e4d8a6c2f190"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _audit_columns() -> tuple[sa.Column[object], sa.Column[object], sa.Column[object]]:
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    """Create immutable plans, variants, assignments, and telemetry."""

    op.create_table(
        "experiments",
        sa.Column("experiment_key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            "plan_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_experiments_plan_fingerprint_format",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'running', 'stopped', 'completed')",
            name="ck_experiments_status",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiments")),
        sa.UniqueConstraint(
            "plan_fingerprint",
            name=op.f("uq_experiments_plan_fingerprint"),
        ),
    )
    op.create_index(
        op.f("ix_experiments_experiment_key"),
        "experiments",
        ["experiment_key"],
        unique=True,
    )
    op.create_index(op.f("ix_experiments_status"), "experiments", ["status"])

    op.create_table(
        "experiment_variants",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("variant_key", sa.String(length=50), nullable=False),
        sa.Column("allocation_basis_points", sa.Integer(), nullable=False),
        sa.Column("is_control", sa.Boolean(), nullable=False),
        sa.Column(
            "configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "allocation_basis_points > 0 AND allocation_basis_points < 10000",
            name="ck_experiment_variants_allocation_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_experiment_variants_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_variants")),
        sa.UniqueConstraint(
            "experiment_id",
            "variant_key",
            name="uq_experiment_variants_experiment_key",
        ),
    )
    op.create_index(
        op.f("ix_experiment_variants_experiment_id"),
        "experiment_variants",
        ["experiment_id"],
    )

    op.create_table(
        "experiment_assignments",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("unit_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "unit_hash ~ '^[0-9a-f]{64}$'",
            name="ck_experiment_assignments_unit_hash_format",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_experiment_assignments_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["experiment_variants.id"],
            name=op.f("fk_experiment_assignments_variant_id_experiment_variants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_assignments")),
        sa.UniqueConstraint(
            "experiment_id",
            "unit_hash",
            name="uq_experiment_assignments_experiment_unit",
        ),
    )
    op.create_index(
        op.f("ix_experiment_assignments_experiment_id"),
        "experiment_assignments",
        ["experiment_id"],
    )
    op.create_index(
        op.f("ix_experiment_assignments_variant_id"),
        "experiment_assignments",
        ["variant_id"],
    )

    op.create_table(
        "experiment_events",
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("metric_name", sa.String(length=100), nullable=True),
        sa.Column("metric_value", sa.Numeric(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "event_type IN ('exposure', 'outcome')",
            name="ck_experiment_events_type",
        ),
        sa.CheckConstraint(
            "(event_type = 'exposure' AND metric_name IS NULL AND metric_value IS NULL) "
            "OR (event_type = 'outcome' AND metric_name IS NOT NULL "
            "AND metric_value IS NOT NULL)",
            name="ck_experiment_events_shape",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["experiment_assignments.id"],
            name=op.f("fk_experiment_events_assignment_id_experiment_assignments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_events")),
        sa.UniqueConstraint(
            "assignment_id",
            "event_key",
            name="uq_experiment_events_assignment_event_key",
        ),
    )
    op.create_index(
        op.f("ix_experiment_events_assignment_id"),
        "experiment_events",
        ["assignment_id"],
    )
    op.create_index(
        op.f("ix_experiment_events_event_type"),
        "experiment_events",
        ["event_type"],
    )
    op.create_index(
        op.f("ix_experiment_events_metric_name"),
        "experiment_events",
        ["metric_name"],
    )
    op.create_index(
        "uq_experiment_events_assignment_exposure",
        "experiment_events",
        ["assignment_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'exposure'"),
    )
    op.create_index(
        "uq_experiment_events_assignment_metric",
        "experiment_events",
        ["assignment_id", "metric_name"],
        unique=True,
        postgresql_where=sa.text("event_type = 'outcome'"),
    )


def downgrade() -> None:
    """Remove controlled experiment storage in dependency order."""

    op.drop_index(
        "uq_experiment_events_assignment_metric",
        table_name="experiment_events",
    )
    op.drop_index(
        "uq_experiment_events_assignment_exposure",
        table_name="experiment_events",
    )
    op.drop_index(op.f("ix_experiment_events_metric_name"), table_name="experiment_events")
    op.drop_index(op.f("ix_experiment_events_event_type"), table_name="experiment_events")
    op.drop_index(op.f("ix_experiment_events_assignment_id"), table_name="experiment_events")
    op.drop_table("experiment_events")
    op.drop_index(
        op.f("ix_experiment_assignments_variant_id"),
        table_name="experiment_assignments",
    )
    op.drop_index(
        op.f("ix_experiment_assignments_experiment_id"),
        table_name="experiment_assignments",
    )
    op.drop_table("experiment_assignments")
    op.drop_index(
        op.f("ix_experiment_variants_experiment_id"),
        table_name="experiment_variants",
    )
    op.drop_table("experiment_variants")
    op.drop_index(op.f("ix_experiments_status"), table_name="experiments")
    op.drop_index(op.f("ix_experiments_experiment_key"), table_name="experiments")
    op.drop_table("experiments")
