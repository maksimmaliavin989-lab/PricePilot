"""Baseline: workspaces and suppliers.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pricepilot.infrastructure.db.tables import UTCDateTime

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("locale", sa.String(35), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("storage_mode", sa.String(16), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_workspaces_version_positive")),
        sa.CheckConstraint(
            "storage_mode IN ('local')", name=op.f("ck_workspaces_storage_mode_allowed")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_index(
        op.f("ix_workspaces_created_at_id"), "workspaces", ["created_at", "id"], unique=False
    )

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(400), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("default_currency", sa.String(3), nullable=False),
        sa.Column("tax_id", sa.String(64), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("archived_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_suppliers_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_suppliers_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
    )
    op.create_index(
        op.f("ix_suppliers_workspace_id_created_at_id"),
        "suppliers",
        ["workspace_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_suppliers_workspace_id_name_key"),
        "suppliers",
        ["workspace_id", "name_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_suppliers_workspace_id_name_key"), table_name="suppliers")
    op.drop_index(op.f("ix_suppliers_workspace_id_created_at_id"), table_name="suppliers")
    op.drop_table("suppliers")
    op.drop_index(op.f("ix_workspaces_created_at_id"), table_name="workspaces")
    op.drop_table("workspaces")
