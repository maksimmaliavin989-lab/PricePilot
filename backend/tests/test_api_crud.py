from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import ClientFactory

WS = {"name": "Закупки", "locale": "ru-RU", "base_currency": "RUB", "country_code": "RU"}


def _workspace(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/v1/workspaces", json=WS)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _supplier(client: TestClient, workspace_id: str, name: str = "ООО Ромашка") -> dict[str, Any]:
    response = client.post(
        "/api/v1/suppliers",
        json={
            "workspace_id": workspace_id,
            "name": name,
            "country_code": "ru",
            "default_currency": "rub",
            "tax_id": "7700000000",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_workspace_create_get_list(client: TestClient) -> None:
    ws = _workspace(client)
    assert ws["storage_mode"] == "local"
    assert ws["version"] == 1
    assert ws["created_at"].endswith("Z") or ws["created_at"].endswith("+00:00")

    got = client.get(f"/api/v1/workspaces/{ws['id']}")
    assert got.status_code == 200
    assert got.headers["etag"] == '"1"'
    assert got.json() == ws

    listed = client.get("/api/v1/workspaces").json()
    assert [w["id"] for w in listed["items"]] == [ws["id"]]
    assert listed["next_cursor"] is None


def test_workspace_patch_optimistic_concurrency(client: TestClient) -> None:
    ws = _workspace(client)
    url = f"/api/v1/workspaces/{ws['id']}"

    missing = client.patch(url, json={"name": "Новое"})
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "PRECONDITION_REQUIRED"

    ok = client.patch(url, json={"name": "Новое"}, headers={"If-Match": '"1"'})
    assert ok.status_code == 200
    assert ok.json()["version"] == 2
    assert ok.headers["etag"] == '"2"'

    stale = client.patch(url, json={"name": "Ещё"}, headers={"If-Match": '"1"'})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["details"]["current_version"] == 2


def test_validation_errors_use_envelope_and_do_not_echo_input(client: TestClient) -> None:
    secret_like = "ПЕРСОНАЛЬНЫЕ-ДАННЫЕ"
    response = client.post(
        "/api/v1/workspaces", json={**WS, "base_currency": "RUBL", "extra": secret_like}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["correlation_id"] == response.headers["x-correlation-id"]
    assert secret_like not in response.text

    domain = client.post("/api/v1/workspaces", json={**WS, "locale": "russian"})
    assert domain.status_code == 422
    assert domain.json()["error"]["details"]["fields"][0]["loc"] == ["body", "locale"]


def test_supplier_crud_search_and_archive(client: TestClient) -> None:
    ws = _workspace(client)
    a = _supplier(client, ws["id"], "ООО Ромашка")
    _supplier(client, ws["id"], "Global Parts Ltd")
    assert a["country_code"] == "RU"
    assert a["default_currency"] == "RUB"

    found = client.get("/api/v1/suppliers", params={"workspace_id": ws["id"], "query": "РОМАШ"})
    assert [s["id"] for s in found.json()["items"]] == [a["id"]]

    wildcard = client.get("/api/v1/suppliers", params={"workspace_id": ws["id"], "query": "%"})
    assert wildcard.json()["items"] == []

    url = f"/api/v1/suppliers/{a['id']}"
    cleared = client.patch(
        url, json={"tax_id": None, "terms": "Отсрочка 30 дней"}, headers={"If-Match": "1"}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["tax_id"] is None
    assert cleared.json()["terms"] == "Отсрочка 30 дней"

    archived = client.patch(url, json={"archived": True}, headers={"If-Match": '"2"'})
    assert archived.json()["archived_at"] is not None
    active = client.get("/api/v1/suppliers", params={"workspace_id": ws["id"]}).json()["items"]
    assert a["id"] not in [s["id"] for s in active]
    everything = client.get(
        "/api/v1/suppliers", params={"workspace_id": ws["id"], "include_archived": True}
    ).json()["items"]
    assert a["id"] in [s["id"] for s in everything]


def test_supplier_requires_existing_workspace(client: TestClient) -> None:
    response = client.post(
        "/api/v1/suppliers",
        json={
            "workspace_id": "00000000-0000-4000-8000-000000000000",
            "name": "X",
            "country_code": "RU",
            "default_currency": "RUB",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["details"] == {"entity": "workspace"}


def test_cursor_pagination(client: TestClient) -> None:
    ws = _workspace(client)
    created = [_supplier(client, ws["id"], f"Поставщик {i}")["id"] for i in range(5)]
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, Any] = {"workspace_id": ws["id"], "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/api/v1/suppliers", params=params).json()
        seen.extend(s["id"] for s in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == created

    bad = client.get("/api/v1/suppliers", params={"workspace_id": ws["id"], "cursor": "!!"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_CURSOR"


def test_data_persists_across_restart(client_factory: ClientFactory) -> None:
    with client_factory() as first:
        ws = _workspace(first)
        supplier = _supplier(first, ws["id"])
    with client_factory() as second:
        assert second.get(f"/api/v1/workspaces/{ws['id']}").json()["name"] == WS["name"]
        assert second.get(f"/api/v1/suppliers/{supplier['id']}").json() == supplier


def test_unknown_route_and_missing_entity(client: TestClient) -> None:
    unknown = client.get("/api/v1/nope")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "NOT_FOUND"
    missing = client.get("/api/v1/suppliers/00000000-0000-4000-8000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["details"] == {"entity": "supplier"}
