"""add normalized financial facts

Revision ID: e4d8a6c2f190
Revises: 7c4e3f2a1b90
Create Date: 2026-08-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4d8a6c2f190"
down_revision: str | None = "7c4e3f2a1b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create issuer-scoped, exact numeric SEC XBRL observations."""

    op.create_table(
        "financial_facts",
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("observation_key", sa.String(length=64), nullable=False),
        sa.Column("taxonomy", sa.String(length=50), nullable=False),
        sa.Column("concept", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=10), nullable=True),
        sa.Column("form_type", sa.String(length=20), nullable=False),
        sa.Column("accession_number", sa.String(length=20), nullable=False),
        sa.Column("frame", sa.String(length=50), nullable=True),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "observation_key ~ '^[0-9a-f]{64}$'",
            name="ck_financial_facts_observation_key_format",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_financial_facts_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_facts")),
        sa.UniqueConstraint(
            "observation_key",
            name=op.f("uq_financial_facts_observation_key"),
        ),
    )
    op.create_index(
        op.f("ix_financial_facts_accession_number"),
        "financial_facts",
        ["accession_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_facts_company_id"),
        "financial_facts",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_financial_facts_company_concept_period",
        "financial_facts",
        ["company_id", "taxonomy", "concept", "end_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_facts_concept"),
        "financial_facts",
        ["concept"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_facts_end_date"),
        "financial_facts",
        ["end_date"],
        unique=False,
    )


def downgrade() -> None:
    """Remove normalized financial facts."""

    op.drop_index(op.f("ix_financial_facts_end_date"), table_name="financial_facts")
    op.drop_index(op.f("ix_financial_facts_concept"), table_name="financial_facts")
    op.drop_index(
        "ix_financial_facts_company_concept_period",
        table_name="financial_facts",
    )
    op.drop_index(op.f("ix_financial_facts_company_id"), table_name="financial_facts")
    op.drop_index(
        op.f("ix_financial_facts_accession_number"),
        table_name="financial_facts",
    )
    op.drop_table("financial_facts")
