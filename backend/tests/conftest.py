from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricepilot.api.app import build_services, create_app
from pricepilot.api.deps import Services
from pricepilot.config import Settings
from pricepilot.infrastructure.db.engine import build_engine, sqlite_url
from pricepilot.infrastructure.db.migrate import upgrade_to_head

UI_ORIGIN = "http://localhost:5173"
UPLOAD_LIMIT = 2 * 1024 * 1024  # small, so oversize tests stay fast
BASE_URL = "http://127.0.0.1"


@pytest.fixture
def token() -> str:
    return secrets.token_urlsafe(32)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


ClientFactory = Callable[[], AbstractContextManager[TestClient]]


@pytest.fixture
def client_factory(data_dir: Path, token: str) -> ClientFactory:
    """Starts a fresh app (full lifespan) over the same data dir — simulates app restarts."""

    @contextmanager
    def _make() -> Iterator[TestClient]:
        settings = Settings(
            data_dir=data_dir,
            api_token=token,
            allowed_origins=("tauri://localhost", UI_ORIGIN),
            log_to_file=False,
            worker_mode="inline",
            max_upload_bytes=UPLOAD_LIMIT,
        )
        with TestClient(
            create_app(settings),
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            yield client

    return _make


@pytest.fixture
def client(client_factory: ClientFactory) -> Iterator[TestClient]:
    with client_factory() as c:
        yield c


# --- Database engines -------------------------------------------------------------------
# SQLite always runs. PostgreSQL runs when PRICEPILOT_TEST_POSTGRES_URL points to a disposable
# database (CI provides one); its ``public`` schema is dropped and recreated per test.

POSTGRES_ENV = "PRICEPILOT_TEST_POSTGRES_URL"


def _reset_postgres(url: str) -> Engine:
    engine = build_engine(url)
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    return engine


@pytest.fixture(params=["sqlite", "postgresql"])
def bare_engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    """Empty database, no migrations applied."""
    if request.param == "sqlite":
        engine = build_engine(sqlite_url(tmp_path / "test.sqlite3"))
    else:
        url = os.environ.get(POSTGRES_ENV)
        if not url:
            pytest.skip(f"{POSTGRES_ENV} is not set")
        engine = _reset_postgres(url)
    yield engine
    engine.dispose()


@pytest.fixture
def engine(bare_engine: Engine) -> Engine:
    upgrade_to_head(bare_engine)
    return bare_engine


@pytest.fixture
def svc(engine: Engine, tmp_path: Path) -> Services:
    return build_services(engine, tmp_path / "files")
