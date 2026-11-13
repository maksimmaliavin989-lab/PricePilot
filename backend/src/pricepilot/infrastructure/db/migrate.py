"""Programmatic Alembic runner. Migrations ship inside the package (sidecar-friendly)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def alembic_config(engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["engine"] = engine
    return config


def upgrade_to_head(engine: Engine) -> None:
    command.upgrade(alembic_config(engine), "head")


def downgrade_to_base(engine: Engine) -> None:
    command.downgrade(alembic_config(engine), "base")
