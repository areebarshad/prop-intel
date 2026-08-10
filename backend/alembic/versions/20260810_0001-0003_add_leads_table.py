"""Add leads table for marketing landing page demo-request submissions.

Revision ID: 0003_add_leads
Revises: 0002_canonical_project
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_add_leads"
down_revision: str = "0002_canonical_project"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("work_email", sa.String(length=320), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=200), nullable=True),
        sa.Column("use_case_notes", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
    )
    op.create_index(op.f("ix_leads_work_email"), "leads", ["work_email"], unique=False)
    op.create_index(op.f("ix_leads_created_at"), "leads", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_leads_created_at"), table_name="leads")
    op.drop_index(op.f("ix_leads_work_email"), table_name="leads")
    op.drop_table("leads")
