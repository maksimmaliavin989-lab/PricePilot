"""Table definitions (SQLAlchemy Core). No business rules live here.

Each module owns its tables: ``workspaces`` → Identity & Workspace,
``suppliers`` → Supplier Registry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

# Portable JSON: TEXT-backed JSON on SQLite, binary JSONB on PostgreSQL. Business code never
# relies on engine-specific JSON operators; decimals inside JSON are always strings.
PortableJSON = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC moments on every engine.

    SQLite has no timezone type: values are stored as naive UTC and re-tagged on read,
    PostgreSQL uses ``timestamptz``.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError("unexpected datetime representation")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _audit_columns() -> list[Column[Any]]:
    return [
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        Column("version", Integer, nullable=False),
    ]


workspaces = Table(
    "workspaces",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("locale", String(35), nullable=False),
    Column("base_currency", String(3), nullable=False),
    Column("country_code", String(2), nullable=False),
    Column("storage_mode", String(16), nullable=False),
    Column("settings_json", JSON(), nullable=False),
    *_audit_columns(),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("storage_mode IN ('local')", name="storage_mode_allowed"),
    Index(None, "created_at", "id"),
)

suppliers = Table(
    "suppliers",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column(
        "workspace_id",
        Uuid(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("name", String(200), nullable=False),
    Column("name_key", String(400), nullable=False),
    Column("country_code", String(2), nullable=False),
    Column("default_currency", String(3), nullable=False),
    Column("tax_id", String(64), nullable=True),
    Column("terms", Text(), nullable=True),
    Column("archived_at", UTCDateTime(), nullable=True),
    *_audit_columns(),
    CheckConstraint("version >= 1", name="version_positive"),
    Index(None, "workspace_id", "created_at", "id"),
    Index(None, "workspace_id", "name_key"),
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


DOCUMENT_STATUSES = (
    "stored",
    "processing",
    "review_required",
    "completed",
    "failed",
    "unsupported",
)
RUN_STATUSES = (
    "queued",
    "validating",
    "parsing",
    "ocr",
    "extracting",
    "validating_result",
    "review_required",
    "completed",
    "failed",
    "cancelled",
    "retry_wait",
)
REVIEW_STATUSES = ("unreviewed", "accepted", "corrected", "rejected")
LINE_STATUSES = ("pending", "review_required", "accepted", "rejected")

# --- Document Intake / DIP -------------------------------------------------------------

source_documents = Table(
    "source_documents",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column(
        "workspace_id", Uuid(), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("supplier_id", Uuid(), ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=True),
    Column("original_name", String(255), nullable=False),
    Column("detected_mime", String(127), nullable=False),
    Column("extension", String(10), nullable=False),
    Column("size_bytes", BigInteger(), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("storage_key", String(255), nullable=False),
    Column("import_source", String(16), nullable=False),
    Column("status", String(24), nullable=False),
    Column("retention_state", String(16), nullable=False),
    *_audit_columns(),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
    CheckConstraint(_in("status", DOCUMENT_STATUSES), name="status_allowed"),
    CheckConstraint(_in("retention_state", ("active", "archived")), name="retention_allowed"),
    UniqueConstraint("workspace_id", "sha256"),
    Index(None, "workspace_id", "created_at", "id"),
    Index(None, "workspace_id", "status"),
    Index(None, "supplier_id"),
)

processing_runs = Table(
    "processing_runs",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column(
        "document_id",
        Uuid(),
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("pipeline_version", String(64), nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("status", String(24), nullable=False),
    Column("attempt", Integer(), nullable=False),
    Column("progress_stage", String(64), nullable=True),
    Column("progress_completed", Integer(), nullable=True),
    Column("progress_total", Integer(), nullable=True),
    Column("error_code", String(64), nullable=True),
    Column("error_retryable", Boolean(), nullable=True),
    Column("diagnostics_ref", String(255), nullable=True),
    Column("started_at", UTCDateTime(), nullable=True),
    Column("finished_at", UTCDateTime(), nullable=True),
    *_audit_columns(),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("attempt >= 1", name="attempt_positive"),
    CheckConstraint(_in("status", RUN_STATUSES), name="status_allowed"),
    UniqueConstraint("document_id", "pipeline_version", "config_hash"),
    Index(None, "document_id", "created_at", "id"),
    Index(None, "status"),
)

extracted_lines = Table(
    "extracted_lines",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column("run_id", Uuid(), ForeignKey("processing_runs.id", ondelete="RESTRICT"), nullable=False),
    Column("ordinal", Integer(), nullable=False),
    Column("raw_text", Text(), nullable=False),
    Column("sheet", String(255), nullable=True),
    Column("page", Integer(), nullable=True),
    Column("row_index", Integer(), nullable=True),
    Column("column_index", Integer(), nullable=True),
    Column("cell", String(16), nullable=True),
    Column("region_json", PortableJSON, nullable=True),
    Column("line_status", String(24), nullable=False),
    Column("review_reasons_json", PortableJSON, nullable=True),
    CheckConstraint("ordinal >= 1", name="ordinal_positive"),
    CheckConstraint(_in("line_status", LINE_STATUSES), name="status_allowed"),
    UniqueConstraint("run_id", "ordinal"),
)

extracted_fields = Table(
    "extracted_fields",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column(
        "line_id", Uuid(), ForeignKey("extracted_lines.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("field_key", String(64), nullable=False),
    Column("value_type", String(24), nullable=False),
    Column("raw_value", Text(), nullable=True),
    Column("normalized_value_json", PortableJSON, nullable=True),
    Column("confidence", Float(), nullable=True),
    Column("provenance_json", PortableJSON, nullable=False),
    Column("review_status", String(16), nullable=False),
    Column("revision", Integer(), nullable=False),
    Column("reviewed_by", String(64), nullable=True),
    Column("reviewed_at", UTCDateTime(), nullable=True),
    CheckConstraint("revision >= 1", name="revision_positive"),
    CheckConstraint(
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
    ),
    CheckConstraint(_in("review_status", REVIEW_STATUSES), name="review_status_allowed"),
    UniqueConstraint("line_id", "field_key"),
)

extracted_field_revisions = Table(
    "extracted_field_revisions",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column(
        "field_id", Uuid(), ForeignKey("extracted_fields.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("revision", Integer(), nullable=False),
    Column("action", String(16), nullable=False),
    Column("previous_value_json", PortableJSON, nullable=True),
    Column("new_value_json", PortableJSON, nullable=True),
    Column("previous_status", String(16), nullable=False),
    Column("new_status", String(16), nullable=False),
    Column("reason", Text(), nullable=True),
    Column("actor_id", String(64), nullable=False),
    Column("occurred_at", UTCDateTime(), nullable=False),
    CheckConstraint(_in("action", ("correct", "accept", "reject")), name="action_allowed"),
    UniqueConstraint("field_id", "revision"),
)

# --- Product Catalog -------------------------------------------------------------------

products = Table(
    "products",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column(
        "workspace_id", Uuid(), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("canonical_name", String(300), nullable=False),
    Column("name_key", String(600), nullable=False),
    Column("brand", String(200), nullable=True),
    Column("manufacturer_code", String(128), nullable=True),
    Column("attributes_json", PortableJSON, nullable=False),
    Column("base_unit_code", String(16), nullable=False),
    Column("active", Boolean(), nullable=False),
    *_audit_columns(),
    CheckConstraint("version >= 1", name="version_positive"),
    Index(None, "workspace_id", "created_at", "id"),
    Index(None, "workspace_id", "name_key"),
)

product_identifiers = Table(
    "product_identifiers",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column("product_id", Uuid(), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
    Column("scheme", String(32), nullable=False),
    Column("value", String(256), nullable=False),
    Column("normalized_value", String(128), nullable=False),
    Column("issuer", String(200), nullable=True),
    CheckConstraint(
        _in("scheme", ("gtin", "supplier_sku", "manufacturer_code", "other")),
        name="scheme_allowed",
    ),
    UniqueConstraint("product_id", "scheme", "normalized_value"),
    Index(None, "scheme", "normalized_value"),
)

# --- Audit -----------------------------------------------------------------------------

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column(
        "workspace_id", Uuid(), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("actor_id", String(64), nullable=True),
    Column("entity_type", String(64), nullable=False),
    Column("entity_id", Uuid(), nullable=False),
    Column("action", String(64), nullable=False),
    Column("before_json", PortableJSON, nullable=True),
    Column("after_json", PortableJSON, nullable=True),
    Column("occurred_at", UTCDateTime(), nullable=False),
    Column("correlation_id", String(64), nullable=True),
    Index(None, "workspace_id", "occurred_at"),
    Index(None, "entity_type", "entity_id"),
)
