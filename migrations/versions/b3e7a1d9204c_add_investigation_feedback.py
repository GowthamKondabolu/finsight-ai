"""add investigation feedback

Revision ID: b3e7a1d9204c
Revises: a9f4c2d7810b
Create Date: 2026-08-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3e7a1d9204c"
down_revision: str | None = "a9f4c2d7810b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create bounded, idempotent analyst feedback storage."""

    op.create_table(
        "investigation_feedback",
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("feedback_key", sa.String(length=200), nullable=False),
        sa.Column("rating", sa.String(length=20), nullable=False),
        sa.Column("evidence_quality", sa.Integer(), nullable=False),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
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
            "rating IN ('helpful', 'not_helpful')",
            name="ck_investigation_feedback_rating",
        ),
        sa.CheckConstraint(
            "evidence_quality >= 1 AND evidence_quality <= 5",
            name="ck_investigation_feedback_evidence_quality",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investigation_feedback")),
        sa.UniqueConstraint(
            "thread_id",
            "feedback_key",
            name="uq_investigation_feedback_thread_key",
        ),
    )
    op.create_index(
        op.f("ix_investigation_feedback_thread_id"),
        "investigation_feedback",
        ["thread_id"],
    )
    op.create_index(
        op.f("ix_investigation_feedback_rating"),
        "investigation_feedback",
        ["rating"],
    )


def downgrade() -> None:
    """Remove analyst feedback storage."""

    op.drop_index(
        op.f("ix_investigation_feedback_rating"),
        table_name="investigation_feedback",
    )
    op.drop_index(
        op.f("ix_investigation_feedback_thread_id"),
        table_name="investigation_feedback",
    )
    op.drop_table("investigation_feedback")
