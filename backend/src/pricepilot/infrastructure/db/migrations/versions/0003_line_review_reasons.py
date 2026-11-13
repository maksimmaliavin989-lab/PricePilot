"""Line-level review reasons (why a person must check an extracted line).

Revision ID: 0003_line_review_reasons
Revises: 0002_documents_catalog
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_line_review_reasons"
down_revision: str | None = "0002_documents_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("extracted_lines", schema=None) as batch_op:
        batch_op.add_column(sa.Column("review_reasons_json", JSONType, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("extracted_lines", schema=None) as batch_op:
        batch_op.drop_column("review_reasons_json")
