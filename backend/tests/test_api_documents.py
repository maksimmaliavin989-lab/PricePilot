"""HTTP contract tests for documents, runs, review and catalog (API v1)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from pricepilot.api.deps import Services
from pricepilot.domain.documents import RunStatus
from tests.factories import Seeded, price_line, seed


def _svc(client: TestClient) -> Services:
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services


@pytest.fixture
def seeded(client: TestClient) -> Seeded:
    return seed(_svc(client), lines=5)


def _first_price(client: TestClient, document_id: str) -> dict[str, Any]:
    body = client.get(f"/api/v1/documents/{document_id}/lines").json()
    field: dict[str, Any] = next(f for f in body["items"][0]["fields"] if f["field_key"] == "price")
    return field


def test_document_list_get_and_filters(client: TestClient, seeded: Seeded) -> None:
    ws = str(seeded.workspace_id)
    listed = client.get("/api/v1/documents", params={"workspace_id": ws}).json()
    assert [d["id"] for d in listed["items"]] == [str(seeded.document.id)]
    doc = listed["items"][0]
    assert doc["status"] == "review_required"
    assert doc["latest_run_id"] == str(seeded.run.id)
    assert "storage_key" not in doc  # internal storage location is not exposed

    none = client.get(
        "/api/v1/documents", params={"workspace_id": ws, "status": "completed"}
    ).json()
    assert none["items"] == []
    bad = client.get("/api/v1/documents", params={"workspace_id": ws, "status": "done"})
    assert bad.status_code == 422

    got = client.get(f"/api/v1/documents/{seeded.document.id}")
    assert got.headers["etag"] == f'"{seeded.document.stamp.version}"'


def test_lines_contract_provenance_and_decimal_strings(client: TestClient, seeded: Seeded) -> None:
    body = client.get(f"/api/v1/documents/{seeded.document.id}/lines", params={"limit": 2}).json()
    assert body["run_id"] == str(seeded.run.id)
    assert [line["ordinal"] for line in body["items"]] == [1, 2]
    assert body["next_cursor"]
    line = body["items"][0]
    assert line["source"] == {
        "sheet": "Прайс",
        "page": None,
        "row": 5,
        "column": None,
        "cell": None,
        "region": None,
        "localization": "approximate",
    }
    price = next(f for f in line["fields"] if f["field_key"] == "price")
    assert price["normalized_value"] == {"amount": "1234.50", "currency": "RUB"}
    assert isinstance(price["normalized_value"]["amount"], str)
    assert price["raw_value"] == "1234,50"
    assert price["provenance"]["cell"] == "D5"
    assert price["provenance"]["pipeline_version"] == seeded.run.pipeline_version
    assert price["review_status"] == "unreviewed"

    rest = client.get(
        f"/api/v1/documents/{seeded.document.id}/lines",
        params={"cursor": body["next_cursor"], "limit": 10},
    ).json()
    assert [line["ordinal"] for line in rest["items"]] == [3, 4, 5]
    assert rest["next_cursor"] is None
    bad = client.get(f"/api/v1/documents/{seeded.document.id}/lines", params={"cursor": "zz"})
    assert bad.json()["error"]["code"] == "INVALID_CURSOR"


def test_field_correction_contract(client: TestClient, seeded: Seeded) -> None:
    price = _first_price(client, str(seeded.document.id))
    url = f"/api/v1/extracted-fields/{price['id']}"

    as_float = client.patch(
        url, json={"value": {"amount": 1199.5, "currency": "RUB"}, "expected_revision": 1}
    )
    assert as_float.status_code == 422
    assert as_float.json()["error"]["details"]["fields"][0]["loc"] == ["body", "value.amount"]

    comma = client.patch(
        url, json={"value": {"amount": "1,199", "currency": "RUB"}, "expected_revision": 1}
    )
    assert comma.status_code == 422

    ok = client.patch(
        url,
        json={
            "value": {"amount": "1199.00", "currency": "rub"},
            "reason": "Опечатка в прайсе",
            "expected_revision": 1,
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["normalized_value"] == {"amount": "1199.00", "currency": "RUB"}
    assert body["raw_value"] == price["raw_value"]
    assert (body["review_status"], body["revision"]) == ("corrected", 2)
    assert body["reviewed_by"] == "local:owner"

    stale = client.patch(
        url, json={"value": {"amount": "1", "currency": "RUB"}, "expected_revision": 1}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_revision"] == 2

    decided = client.post(f"{url}/decision", json={"decision": "accept", "expected_revision": 2})
    assert decided.json()["review_status"] == "accepted"

    history = client.get(f"{url}/revisions").json()
    assert [(h["revision"], h["action"]) for h in history] == [(2, "correct"), (3, "accept")]
    assert history[0]["previous_value"] == {"amount": "1234.50", "currency": "RUB"}
    assert history[0]["reason"] == "Опечатка в прайсе"

    missing = client.get("/api/v1/extracted-fields/00000000-0000-4000-8000-000000000000")
    assert missing.json()["error"]["details"] == {"entity": "extracted_field"}


def test_run_status_cancel_and_retry(client: TestClient) -> None:
    svc = _svc(client)
    s = seed(svc, lines=0, finish=None)
    run_url = f"/api/v1/processing-runs/{s.run.id}"
    body = client.get(run_url).json()
    assert (body["status"], body["counts"]["lines"]) == ("extracting", 0)

    not_retryable = client.post(f"{run_url}/retry")
    assert not_retryable.status_code == 409
    assert not_retryable.json()["error"]["code"] == "INVALID_STATE"

    cancelled = client.post(f"{run_url}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == {"code": "CANCELLED", "retryable": False}
    doc = client.get(f"/api/v1/documents/{s.document.id}").json()
    assert doc["status"] == "stored"  # not "processed"

    run, _ = svc.processing.start(s.document.id, "dip-test-2", "d" * 64)
    svc.processing.advance(run.id, RunStatus.VALIDATING)
    svc.processing.fail(run.id, "STORAGE_ERROR", retryable=True)
    retried = client.post(f"/api/v1/processing-runs/{run.id}/retry").json()
    assert (retried["status"], retried["attempt"]) == ("queued", 2)


def test_lines_for_unknown_run_or_foreign_run(client: TestClient, seeded: Seeded) -> None:
    svc = _svc(client)
    other = seed(svc, lines=1, name="Прайс_2.xlsx")
    foreign = client.get(
        f"/api/v1/documents/{seeded.document.id}/lines", params={"run_id": str(other.run.id)}
    )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["details"] == {"entity": "processing_run"}


def test_products_contract(client: TestClient) -> None:
    ws = client.post(
        "/api/v1/workspaces",
        json={"name": "WS", "locale": "ru-RU", "base_currency": "RUB", "country_code": "RU"},
    ).json()
    created = client.post(
        "/api/v1/products",
        json={
            "workspace_id": ws["id"],
            "canonical_name": "Штукатурка гипсовая 30 кг",
            "base_unit_code": "bag",
            "attributes": {"масса": "30 кг"},
            "identifiers": [
                {"scheme": "gtin", "value": "4006381333931"},
                {"scheme": "supplier_sku", "value": "СМ-101", "issuer": "ООО Ромашка"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    product = created.json()
    assert product["identifiers"][0]["normalized_value"] == "04006381333931"
    assert client.get(f"/api/v1/products/{product['id']}").json() == product
    listed = client.get(
        "/api/v1/products", params={"workspace_id": ws["id"], "query": "ШТУКАТУРКА"}
    ).json()
    assert [p["id"] for p in listed["items"]] == [product["id"]]

    bad_gtin = client.post(
        "/api/v1/products",
        json={
            "workspace_id": ws["id"],
            "canonical_name": "X",
            "base_unit_code": "pc",
            "identifiers": [{"scheme": "gtin", "value": "4006381333932"}],
        },
    )
    assert bad_gtin.status_code == 422
    bad_unit = client.post(
        "/api/v1/products",
        json={"workspace_id": ws["id"], "canonical_name": "X", "base_unit_code": "мешок"},
    )
    assert bad_unit.json()["error"]["details"]["fields"][0]["loc"] == ["body", "base_unit_code"]


def test_units_vocabulary(client: TestClient) -> None:
    units = {u["code"]: u for u in client.get("/api/v1/units").json()}
    assert units["m3"]["label_ru"] == "м³"
    assert units["pc"]["dimension"] == "count"


def test_replayed_lines_return_conflict(client: TestClient) -> None:
    svc = _svc(client)
    s = seed(svc, lines=1, finish=None)
    from pricepilot.application.errors import DuplicateExtractionError

    with pytest.raises(DuplicateExtractionError):
        svc.processing.record_lines(s.run.id, [price_line(1)], extractor="x", extractor_version="1")
