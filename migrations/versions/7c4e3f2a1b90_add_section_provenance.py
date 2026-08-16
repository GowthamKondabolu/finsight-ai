"""add section provenance metadata

Revision ID: 7c4e3f2a1b90
Revises: 83a0e5d756fb
Create Date: 2026-08-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c4e3f2a1b90"
down_revision: str | None = "83a0e5d756fb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add parser and source-offset metadata to normalized filing sections."""

    op.add_column(
        "filing_sections",
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove normalized filing-section provenance metadata."""

    op.drop_column("filing_sections", "source_metadata")
