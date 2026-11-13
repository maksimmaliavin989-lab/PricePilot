"""TASK_005: pre-migration backup, manual backup/restore, single instance per data dir."""

from __future__ import annotations

import json
import secrets
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import insert

from pricepilot.__main__ import main
from pricepilot.api.app import create_app
from pricepilot.config import Settings
from pricepilot.infrastructure.db.backup import AlreadyRunningError, DataDirLock
from pricepilot.infrastructure.db.engine import build_engine, sqlite_url
from pricepilot.infrastructure.db.migrate import alembic_config
from pricepilot.infrastructure.db.tables import workspaces
from tests.conftest import BASE_URL


def _seed_old_database(data_dir: Path, revision: str) -> str:
    """A database created by an older build (schema at ``revision``) with one workspace."""
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = build_engine(sqlite_url(data_dir / "pricepilot.sqlite3"))
    command.upgrade(alembic_config(engine), revision)
    ws_id = uuid4()
    now = datetime(2026, 11, 1, tzinfo=UTC)
    with engine.begin() as c:
        c.execute(
            insert(workspaces).values(
                id=ws_id,
                name="Старая версия",
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
    engine.dispose()
    return str(ws_id)


def _client(data_dir: Path, token: str) -> TestClient:
    settings = Settings(data_dir=data_dir, api_token=token, log_to_file=False, worker_mode="inline")
    return TestClient(
        create_app(settings), base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}
    )


def _names(data_dir: Path) -> list[str]:
    return sorted(p.name for p in (data_dir / "backups").glob("*.sqlite3"))


def test_upgrade_creates_backup_and_keeps_data(tmp_path: Path, token: str) -> None:
    data_dir = tmp_path / "data"
    ws_id = _seed_old_database(data_dir, "0002_documents_catalog")
    with _client(data_dir, token) as client:
        health = client.get("/api/v1/health").json()
        assert health["schema_revision"] == "0003_line_review_reasons"
        assert health["migration"]["from_revision"] == "0002_documents_catalog"
        assert health["migration"]["to_revision"] == "0003_line_review_reasons"
        assert client.get(f"/api/v1/workspaces/{ws_id}").json()["name"] == "Старая версия"
    backups = _names(data_dir)
    assert backups == [health["migration"]["backup"]]
    copy = sqlite3.connect(data_dir / "backups" / backups[0])
    try:
        assert copy.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0002_documents_catalog",
        )
        assert copy.execute("SELECT name FROM workspaces").fetchall() == [("Старая версия",)]
    finally:
        copy.close()

    # The next start has nothing to migrate: no new backup, no migration notice.
    with _client(data_dir, token) as client:
        assert client.get("/api/v1/health").json()["migration"] is None
    assert _names(data_dir) == backups


def test_fresh_install_needs_no_backup(tmp_path: Path, token: str) -> None:
    data_dir = tmp_path / "data"
    with _client(data_dir, token) as client:
        assert client.get("/api/v1/health").json()["migration"] is None
    assert not (data_dir / "backups").exists()


def test_manual_backup_and_restore_round_trip(tmp_path: Path, token: str) -> None:
    data_dir = tmp_path / "data"
    with _client(data_dir, token) as client:
        ws = client.post(
            "/api/v1/workspaces",
            json={"name": "До копии", "locale": "ru", "base_currency": "RUB", "country_code": "RU"},
        ).json()
        created = client.post("/api/v1/backups")
        assert created.status_code == 201
        backup_name = created.json()["name"]
        assert [b["name"] for b in client.get("/api/v1/backups").json()] == [backup_name]
        client.patch(
            f"/api/v1/workspaces/{ws['id']}",
            json={"name": "После копии"},
            headers={"If-Match": "1"},
        )

    assert main(["restore", "--data-dir", str(data_dir), backup_name]) == 0
    # Restoring backs up the replaced database first.
    assert any("before-restore" in n for n in _names(data_dir))
    with _client(data_dir, token) as client:
        assert client.get(f"/api/v1/workspaces/{ws['id']}").json()["name"] == "До копии"


def test_restore_refused_while_running_and_bad_names(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with DataDirLock(data_dir):
        assert main(["restore", "--data-dir", str(data_dir), "x"]) == 3
    assert main(["restore", "--data-dir", str(data_dir), "../../etc/passwd"]) == 1
    assert (
        main(["restore", "--data-dir", str(data_dir), "pricepilot-20260101T000000Z-x.sqlite3"]) == 1
    )


def test_data_dir_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    first = DataDirLock(tmp_path)
    first.acquire()
    with pytest.raises(AlreadyRunningError), DataDirLock(tmp_path):
        pass
    first.release()
    with DataDirLock(tmp_path):
        pass


def test_second_api_on_same_data_dir_refuses_to_start(tmp_path: Path) -> None:
    holder = DataDirLock(tmp_path)
    holder.acquire()
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pricepilot",
                "serve",
                "--data-dir",
                str(tmp_path),
                "--token-stdin",
            ],
            input=secrets.token_urlsafe(32) + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        holder.release()
    assert result.returncode == 3
    assert json.loads(result.stdout)["code"] == "ALREADY_RUNNING"
