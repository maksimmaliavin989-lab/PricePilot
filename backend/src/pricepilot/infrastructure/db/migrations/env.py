"""Alembic environment. Works with an engine passed programmatically or with
``PRICEPILOT_DATABASE_URL`` when invoked from the Alembic CLI."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import Engine

from pricepilot.infrastructure.db.engine import build_engine
from pricepilot.infrastructure.db.tables import metadata

config = context.config


def _engine() -> Engine:
    engine = config.attributes.get("engine")
    if isinstance(engine, Engine):
        return engine
    url = os.environ.get("PRICEPILOT_DATABASE_URL")
    if not url:
        raise RuntimeError("PRICEPILOT_DATABASE_URL is required for CLI migrations")
    return build_engine(url)


def run_migrations_offline() -> None:
    url = os.environ.get("PRICEPILOT_DATABASE_URL", "sqlite://")
    context.configure(
        url=url,
        target_metadata=metadata,
        literal_binds=True,
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = _engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
