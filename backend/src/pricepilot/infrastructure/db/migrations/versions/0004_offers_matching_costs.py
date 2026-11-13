"""Supplier offers, match decisions, cost policies, FX and tax rates, comparisons and
cost snapshots (TASK_006).

Revision ID: 0004_offers_matching_costs
Revises: 0003_line_review_reasons
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from pricepilot.infrastructure.db.tables import UTCDateTime

revision: str = "0004_offers_matching_costs"
down_revision: str | None = "0003_line_review_reasons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the portable JSON type used by this revision.
JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "comparisons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("target_currency", sa.String(length=3), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_comparisons_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_comparisons_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comparisons")),
    )
    with op.batch_alter_table("comparisons", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_comparisons_workspace_id_created_at_id"),
            ["workspace_id", "created_at", "id"],
            unique=False,
        )

    op.create_table(
        "cost_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("types_json", JSONType, nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_cost_policies_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_cost_policies_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_policies")),
        sa.UniqueConstraint("workspace_id", name=op.f("uq_cost_policies_workspace_id")),
    )
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.String(length=40), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_fx_rates_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_rates")),
    )
    with op.batch_alter_table("fx_rates", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_fx_rates_workspace_id_base_currency_quote_currency_effective_date"),
            ["workspace_id", "base_currency", "quote_currency", "effective_date"],
            unique=False,
        )

    op.create_table(
        "tax_rate_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("reference", sa.String(length=500), nullable=False),
        sa.Column("verified_by", sa.String(length=200), nullable=False),
        sa.Column("verified_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_tax_rate_sources_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_rate_sources")),
    )
    with op.batch_alter_table("tax_rate_sources", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_tax_rate_sources_workspace_id"), ["workspace_id"], unique=False
        )

    op.create_table(
        "comparison_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comparison_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("required_quantity", sa.String(length=40), nullable=False),
        sa.Column("unit_code", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["comparison_id"],
            ["comparisons.id"],
            name=op.f("fk_comparison_items_comparison_id_comparisons"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_comparison_items_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comparison_items")),
        sa.UniqueConstraint(
            "comparison_id", "product_id", name=op.f("uq_comparison_items_comparison_id_product_id")
        ),
    )
    op.create_table(
        "tax_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("component_code", sa.String(length=40), nullable=False),
        sa.Column("rate", sa.String(length=40), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("scope", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["tax_rate_sources.id"],
            name=op.f("fk_tax_rates_source_id_tax_rate_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_rates")),
    )
    with op.batch_alter_table("tax_rates", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tax_rates_source_id"), ["source_id"], unique=False)

    op.create_table(
        "supplier_offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=True),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("extracted_line_id", sa.Uuid(), nullable=False),
        sa.Column("line_ordinal", sa.Integer(), nullable=False),
        sa.Column("price_field_key", sa.String(length=64), nullable=False),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("supplier_sku", sa.String(length=256), nullable=True),
        sa.Column("unit_code", sa.String(length=16), nullable=True),
        sa.Column("price_amount", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_confirmed", sa.Boolean(), nullable=False),
        sa.Column("pack_json", JSONType, nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_supplier_offers_version_positive")),
        sa.ForeignKeyConstraint(
            ["extracted_line_id"],
            ["extracted_lines.id"],
            name=op.f("fk_supplier_offers_extracted_line_id_extracted_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            name=op.f("fk_supplier_offers_source_document_id_source_documents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_supplier_offers_supplier_id_suppliers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_supplier_offers_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_offers")),
        sa.UniqueConstraint(
            "extracted_line_id",
            "price_field_key",
            name=op.f("uq_supplier_offers_extracted_line_id_price_field_key"),
        ),
    )
    with op.batch_alter_table("supplier_offers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_supplier_offers_source_document_id"),
            ["source_document_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_supplier_offers_workspace_id_created_at_id"),
            ["workspace_id", "created_at", "id"],
            unique=False,
        )

    op.create_table(
        "match_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_json", JSONType, nullable=True),
        sa.Column("override", sa.Boolean(), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('accept', 'reject', 'revoke')",
            name=op.f("ck_match_decisions_decision_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["supplier_offers.id"],
            name=op.f("fk_match_decisions_offer_id_supplier_offers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_match_decisions_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["match_decisions.id"],
            name=op.f("fk_match_decisions_supersedes_id_match_decisions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_decisions")),
    )
    with op.batch_alter_table("match_decisions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_match_decisions_offer_id_decided_at"),
            ["offer_id", "decided_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_match_decisions_product_id"), ["product_id"], unique=False
        )

    op.create_table(
        "comparison_offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("match_decision_id", sa.Uuid(), nullable=False),
        sa.Column("terms_json", JSONType, nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["comparison_items.id"],
            name=op.f("fk_comparison_offers_item_id_comparison_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["match_decision_id"],
            ["match_decisions.id"],
            name=op.f("fk_comparison_offers_match_decision_id_match_decisions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["supplier_offers.id"],
            name=op.f("fk_comparison_offers_offer_id_supplier_offers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comparison_offers")),
        sa.UniqueConstraint(
            "item_id", "offer_id", name=op.f("uq_comparison_offers_item_id_offer_id")
        ),
    )
    op.create_table(
        "cost_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comparison_offer_id", sa.Uuid(), nullable=False),
        sa.Column("formula_version", sa.String(length=32), nullable=False),
        sa.Column("policy_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", JSONType, nullable=False),
        sa.Column("total_amount", sa.String(length=40), nullable=True),
        sa.Column("unit_amount", sa.String(length=40), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["comparison_offer_id"],
            ["comparison_offers.id"],
            name=op.f("fk_cost_snapshots_comparison_offer_id_comparison_offers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["cost_snapshots.id"],
            name=op.f("fk_cost_snapshots_supersedes_id_cost_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_snapshots")),
    )
    with op.batch_alter_table("cost_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_cost_snapshots_comparison_offer_id_created_at"),
            ["comparison_offer_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("cost_snapshots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cost_snapshots_comparison_offer_id_created_at"))

    op.drop_table("cost_snapshots")
    op.drop_table("comparison_offers")
    with op.batch_alter_table("match_decisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_match_decisions_product_id"))
        batch_op.drop_index(batch_op.f("ix_match_decisions_offer_id_decided_at"))

    op.drop_table("match_decisions")
    with op.batch_alter_table("supplier_offers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_supplier_offers_workspace_id_created_at_id"))
        batch_op.drop_index(batch_op.f("ix_supplier_offers_source_document_id"))

    op.drop_table("supplier_offers")
    with op.batch_alter_table("tax_rates", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tax_rates_source_id"))

    op.drop_table("tax_rates")
    op.drop_table("comparison_items")
    with op.batch_alter_table("tax_rate_sources", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tax_rate_sources_workspace_id"))

    op.drop_table("tax_rate_sources")
    with op.batch_alter_table("fx_rates", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_fx_rates_workspace_id_base_currency_quote_currency_effective_date")
        )

    op.drop_table("fx_rates")
    op.drop_table("cost_policies")
    with op.batch_alter_table("comparisons", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_comparisons_workspace_id_created_at_id"))

    op.drop_table("comparisons")
