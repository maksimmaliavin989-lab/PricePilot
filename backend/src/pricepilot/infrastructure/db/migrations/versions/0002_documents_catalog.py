"""Documents, processing runs, extraction (lines/fields/revisions), catalog and audit.

Revision ID: 0002_documents_catalog
Revises: 0001_baseline
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from pricepilot.infrastructure.db.tables import UTCDateTime

revision: str = "0002_documents_catalog"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the portable JSON type used by this revision.
JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("before_json", JSONType, nullable=True),
        sa.Column("after_json", JSONType, nullable=True),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_audit_events_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_audit_events_entity_type_entity_id"),
            ["entity_type", "entity_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_audit_events_workspace_id_occurred_at"),
            ["workspace_id", "occurred_at"],
            unique=False,
        )

    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_name", sa.String(length=300), nullable=False),
        sa.Column("name_key", sa.String(length=600), nullable=False),
        sa.Column("brand", sa.String(length=200), nullable=True),
        sa.Column("manufacturer_code", sa.String(length=128), nullable=True),
        sa.Column("attributes_json", JSONType, nullable=False),
        sa.Column("base_unit_code", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_products_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_products_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_products_workspace_id_created_at_id"),
            ["workspace_id", "created_at", "id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_products_workspace_id_name_key"),
            ["workspace_id", "name_key"],
            unique=False,
        )

    op.create_table(
        "product_identifiers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("scheme", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.Column("normalized_value", sa.String(length=128), nullable=False),
        sa.Column("issuer", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "scheme IN ('gtin', 'supplier_sku', 'manufacturer_code', 'other')",
            name=op.f("ck_product_identifiers_scheme_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_identifiers_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_identifiers")),
        sa.UniqueConstraint(
            "product_id",
            "scheme",
            "normalized_value",
            name=op.f("uq_product_identifiers_product_id_scheme_normalized_value"),
        ),
    )
    with op.batch_alter_table("product_identifiers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_product_identifiers_scheme_normalized_value"),
            ["scheme", "normalized_value"],
            unique=False,
        )

    op.create_table(
        "source_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("detected_mime", sa.String(length=127), nullable=False),
        sa.Column("extension", sa.String(length=10), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("import_source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("retention_state", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "retention_state IN ('active', 'archived')",
            name=op.f("ck_source_documents_retention_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('stored', 'processing', 'review_required', 'completed', 'failed', "
            "'unsupported')",
            name=op.f("ck_source_documents_status_allowed"),
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_source_documents_size_nonnegative")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_source_documents_version_positive")),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_source_documents_supplier_id_suppliers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_source_documents_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_documents")),
        sa.UniqueConstraint(
            "workspace_id", "sha256", name=op.f("uq_source_documents_workspace_id_sha256")
        ),
    )
    with op.batch_alter_table("source_documents", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_documents_supplier_id"), ["supplier_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_source_documents_workspace_id_created_at_id"),
            ["workspace_id", "created_at", "id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_source_documents_workspace_id_status"),
            ["workspace_id", "status"],
            unique=False,
        )

    op.create_table(
        "processing_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("progress_stage", sa.String(length=64), nullable=True),
        sa.Column("progress_completed", sa.Integer(), nullable=True),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("diagnostics_ref", sa.String(length=255), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'validating', 'parsing', 'ocr', 'extracting', "
            "'validating_result', 'review_required', 'completed', 'failed', 'cancelled', "
            "'retry_wait')",
            name=op.f("ck_processing_runs_status_allowed"),
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_processing_runs_attempt_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_processing_runs_version_positive")),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["source_documents.id"],
            name=op.f("fk_processing_runs_document_id_source_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_runs")),
        sa.UniqueConstraint(
            "document_id",
            "pipeline_version",
            "config_hash",
            name=op.f("uq_processing_runs_document_id_pipeline_version_config_hash"),
        ),
    )
    with op.batch_alter_table("processing_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_processing_runs_document_id_created_at_id"),
            ["document_id", "created_at", "id"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_processing_runs_status"), ["status"], unique=False)

    op.create_table(
        "extracted_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("sheet", sa.String(length=255), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("row_index", sa.Integer(), nullable=True),
        sa.Column("column_index", sa.Integer(), nullable=True),
        sa.Column("cell", sa.String(length=16), nullable=True),
        sa.Column("region_json", JSONType, nullable=True),
        sa.Column("line_status", sa.String(length=24), nullable=False),
        sa.CheckConstraint(
            "line_status IN ('pending', 'review_required', 'accepted', 'rejected')",
            name=op.f("ck_extracted_lines_status_allowed"),
        ),
        sa.CheckConstraint("ordinal >= 1", name=op.f("ck_extracted_lines_ordinal_positive")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["processing_runs.id"],
            name=op.f("fk_extracted_lines_run_id_processing_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_lines")),
        sa.UniqueConstraint("run_id", "ordinal", name=op.f("uq_extracted_lines_run_id_ordinal")),
    )
    op.create_table(
        "extracted_fields",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.Uuid(), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("value_type", sa.String(length=24), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("normalized_value_json", JSONType, nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("provenance_json", JSONType, nullable=False),
        sa.Column("review_status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "review_status IN ('unreviewed', 'accepted', 'corrected', 'rejected')",
            name=op.f("ck_extracted_fields_review_status_allowed"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_extracted_fields_confidence_range"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_extracted_fields_revision_positive")),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["extracted_lines.id"],
            name=op.f("fk_extracted_fields_line_id_extracted_lines"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_fields")),
        sa.UniqueConstraint(
            "line_id", "field_key", name=op.f("uq_extracted_fields_line_id_field_key")
        ),
    )
    op.create_table(
        "extracted_field_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("previous_value_json", JSONType, nullable=True),
        sa.Column("new_value_json", JSONType, nullable=True),
        sa.Column("previous_status", sa.String(length=16), nullable=False),
        sa.Column("new_status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('correct', 'accept', 'reject')",
            name=op.f("ck_extracted_field_revisions_action_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["extracted_fields.id"],
            name=op.f("fk_extracted_field_revisions_field_id_extracted_fields"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_field_revisions")),
        sa.UniqueConstraint(
            "field_id", "revision", name=op.f("uq_extracted_field_revisions_field_id_revision")
        ),
    )


def downgrade() -> None:
    op.drop_table("extracted_field_revisions")
    op.drop_table("extracted_fields")
    op.drop_table("extracted_lines")
    with op.batch_alter_table("processing_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_processing_runs_status"))
        batch_op.drop_index(batch_op.f("ix_processing_runs_document_id_created_at_id"))

    op.drop_table("processing_runs")
    with op.batch_alter_table("source_documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_documents_workspace_id_status"))
        batch_op.drop_index(batch_op.f("ix_source_documents_workspace_id_created_at_id"))
        batch_op.drop_index(batch_op.f("ix_source_documents_supplier_id"))

    op.drop_table("source_documents")
    with op.batch_alter_table("product_identifiers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_product_identifiers_scheme_normalized_value"))

    op.drop_table("product_identifiers")
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_products_workspace_id_name_key"))
        batch_op.drop_index(batch_op.f("ix_products_workspace_id_created_at_id"))

    op.drop_table("products")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_events_workspace_id_occurred_at"))
        batch_op.drop_index(batch_op.f("ix_audit_events_entity_type_entity_id"))

    op.drop_table("audit_events")
