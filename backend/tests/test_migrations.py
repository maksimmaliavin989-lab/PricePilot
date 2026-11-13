from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, insert, inspect, select, text

from pricepilot.infrastructure.db.migrate import alembic_config, downgrade_to_base, upgrade_to_head
from pricepilot.infrastructure.db.tables import metadata, workspaces

TABLES = {
    "supplier_offers",
    "match_decisions",
    "cost_policies",
    "fx_rates",
    "tax_rate_sources",
    "tax_rates",
    "comparisons",
    "comparison_items",
    "comparison_offers",
    "cost_snapshots",
    "workspaces",
    "suppliers",
    "source_documents",
    "processing_runs",
    "extracted_lines",
    "extracted_fields",
    "extracted_field_revisions",
    "products",
    "product_identifiers",
    "audit_events",
}


def test_upgrade_creates_clean_database(bare_engine: Engine) -> None:
    upgrade_to_head(bare_engine)
    assert TABLES | {"alembic_version"} <= set(inspect(bare_engine).get_table_names())
    with bare_engine.connect() as c:
        for table in TABLES:
            assert c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0  # noqa: S608
        head = ScriptDirectory.from_config(alembic_config(bare_engine)).get_current_head()
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head


def test_upgrade_is_idempotent_and_reversible(bare_engine: Engine) -> None:
    upgrade_to_head(bare_engine)
    upgrade_to_head(bare_engine)
    downgrade_to_base(bare_engine)
    assert set(inspect(bare_engine).get_table_names()) <= {"alembic_version"}
    upgrade_to_head(bare_engine)
    assert set(inspect(bare_engine).get_table_names()) >= TABLES


def test_migrations_match_table_metadata(bare_engine: Engine) -> None:
    upgrade_to_head(bare_engine)
    with bare_engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), metadata)
    assert diff == []


def test_upgrade_from_previous_revision_keeps_data(bare_engine: Engine) -> None:
    command.upgrade(alembic_config(bare_engine), "0001_baseline")
    now = datetime(2026, 11, 13, tzinfo=UTC)
    ws_id = uuid4()
    with bare_engine.begin() as c:
        c.execute(
            insert(workspaces).values(
                id=ws_id,
                name="До миграции",
                locale="ru-RU",
                base_currency="RUB",
                country_code="RU",
                storage_mode="local",
                settings_json={},
                created_at=now,
                updated_at=now,
                version=1,
            )
        )
    upgrade_to_head(bare_engine)
    with bare_engine.connect() as c:
        row = c.execute(select(workspaces).where(workspaces.c.id == ws_id)).one()
    assert (row.name, row.created_at) == ("До миграции", now)


def test_sqlite_foreign_keys_enforced(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
