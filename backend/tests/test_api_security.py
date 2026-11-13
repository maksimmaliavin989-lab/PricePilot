from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pricepilot.api.app import create_app
from pricepilot.config import ConfigError, Settings
from tests.conftest import BASE_URL, UI_ORIGIN

HEALTH = "/api/v1/health"


def test_health_requires_token(client: TestClient) -> None:
    anonymous = client.get(HEALTH, headers={"Authorization": ""})
    assert anonymous.status_code == 401
    body = anonymous.json()["error"]
    assert body["code"] == "UNAUTHORIZED"
    assert body["correlation_id"] == anonymous.headers["x-correlation-id"]

    wrong = client.get(HEALTH, headers={"Authorization": "Bearer " + "x" * 43})
    assert wrong.status_code == 401

    ok = client.get(HEALTH)
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"
    assert ok.json()["storage_mode"] == "local"


def test_non_loopback_host_rejected(client: TestClient) -> None:
    for host in ("evil.example", "192.168.1.10:8000", "127.0.0.1.nip.io"):
        response = client.get(HEALTH, headers={"Host": host})
        assert response.status_code == 400, host
        assert response.json()["error"]["code"] == "INVALID_HOST"
    assert client.get(HEALTH, headers={"Host": "localhost:1234"}).status_code == 200


def test_foreign_origin_rejected_even_with_token(client: TestClient) -> None:
    response = client.get(HEALTH, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ORIGIN"
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_ui_origin_and_preflight(client: TestClient) -> None:
    preflight = client.options(
        "/api/v1/workspaces",
        headers={
            "Origin": UI_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
            "Authorization": "",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == UI_ORIGIN

    actual = client.get(HEALTH, headers={"Origin": UI_ORIGIN})
    assert actual.status_code == 200
    assert actual.headers["access-control-allow-origin"] == UI_ORIGIN
    assert "x-correlation-id" in actual.headers["access-control-expose-headers"].lower()

    denied = client.get(HEALTH, headers={"Origin": UI_ORIGIN, "Authorization": ""})
    assert denied.status_code == 401
    assert denied.headers["access-control-allow-origin"] == UI_ORIGIN  # UI can read the error


def test_correlation_id_propagated_or_generated(client: TestClient) -> None:
    given = client.get(HEALTH, headers={"X-Correlation-Id": "ui-req-12345678"})
    assert given.headers["x-correlation-id"] == "ui-req-12345678"
    bogus = client.get(HEALTH, headers={"X-Correlation-Id": "bad id\nwith newline"})
    assert bogus.headers["x-correlation-id"] != "bad id\nwith newline"
    assert len(bogus.headers["x-correlation-id"]) == 36


def test_body_size_limited(client: TestClient) -> None:
    huge = json.dumps(
        {"name": "x" * 1_100_000, "locale": "ru", "base_currency": "RUB", "country_code": "RU"}
    )
    response = client.post(
        "/api/v1/workspaces", content=huge, headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "LIMIT_EXCEEDED"


def test_openapi_and_docs_not_served(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_token_never_logged(data_dir: Path, token: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    settings = Settings(data_dir=data_dir, api_token=token, log_to_file=False)
    with TestClient(create_app(settings), base_url=BASE_URL) as c:
        c.get(HEALTH, headers={"Authorization": f"Bearer {token}"})
        c.get(HEALTH + f"?leak={token}", headers={"Authorization": f"Bearer {token}"})
        logging.getLogger("pricepilot.test").info("token is %s", token)
    from pricepilot.infrastructure.logging import JsonFormatter

    formatter = JsonFormatter()
    server_records = [r for r in caplog.records if not r.name.startswith("httpx")]  # test client
    rendered = "\n".join(formatter.format(r) for r in server_records)
    assert token not in rendered
    assert "[REDACTED]" in rendered
    assert "leak=" not in rendered  # access log excludes query strings


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_token": "short"},
        {"port": 70000},
        {"allowed_origins": ("https://evil.example",)},
        {"allowed_origins": ("http://localhost:5173/path",)},
    ],
)
def test_settings_reject_unsafe_values(tmp_path: Path, kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {"data_dir": tmp_path, "api_token": "t" * 32}
    with pytest.raises(ConfigError):
        Settings(**{**base, **kwargs})  # type: ignore[arg-type]


def test_host_is_always_loopback(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, api_token="t" * 32)
    assert settings.host == "127.0.0.1"
