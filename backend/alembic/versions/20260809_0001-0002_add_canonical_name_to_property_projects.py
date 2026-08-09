"""Add canonical_name to property_projects for consistent deduplication.

Revision ID: 0002_canonical_project
Revises: 0001_initial
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_canonical_project"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "property_projects",
        sa.Column("canonical_name", sa.String(length=400), nullable=True),
    )
    op.create_index(
        op.f("ix_property_projects_canonical_name"),
        "property_projects",
        ["canonical_name"],
        unique=False,
    )
    # Backfill existing rows using the stored display name as a best-effort
    # canonical_name. A re-ingest will overwrite these with the proper
    # canonicalized form.
    op.execute(
        "UPDATE property_projects SET canonical_name = lower(name) WHERE canonical_name IS NULL"
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_property_projects_canonical_name"), table_name="property_projects"
    )
    op.drop_column("property_projects", "canonical_name")
