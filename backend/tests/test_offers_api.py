"""TASK_006 end to end: price list → offers → matching → comparison → landed cost snapshots."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.test_import_api import FIXTURES, upload, ws  # noqa: F401 - fixture

LIST_1 = FIXTURES / "Прайс_1_obshchestroitelnye.xlsx"
LIST_2 = FIXTURES / "Прайс_2_otdelochnye.xlsx"
API = "/api/v1"


def offers_of(client: TestClient, space: dict[str, Any], doc_id: str) -> list[dict[str, Any]]:
    page = client.get(
        f"{API}/offers", params={"workspace_id": space["id"], "document_id": doc_id, "limit": 200}
    ).json()
    return list(page["items"])


def imported(client: TestClient, space: dict[str, Any], path: Any) -> str:
    body = upload(client, space, path.name, path.read_bytes()).json()
    doc_id: str = body["document"]["id"]
    created = client.post(f"{API}/documents/{doc_id}/offers")
    assert created.status_code == 200, created.text
    return doc_id


def cement(offers: list[dict[str, Any]]) -> dict[str, Any]:
    return next(o for o in offers if o["name"].startswith("Цемент М500"))


def test_offers_are_created_once_with_pack_suggestions(
    client: TestClient,
    ws: dict[str, Any],  # noqa: F811
) -> None:
    body = upload(client, ws, LIST_1.name, LIST_1.read_bytes()).json()
    doc_id = body["document"]["id"]
    first = client.post(f"{API}/documents/{doc_id}/offers").json()
    assert first["created"] == 39
    again = client.post(f"{API}/documents/{doc_id}/offers").json()
    assert (again["created"], again["existing"]) == (0, 39)
    offer = cement(offers_of(client, ws, doc_id))
    assert offer["price"] == {"amount": "540", "currency": "RUB"}
    assert offer["unit"] == "pc"
    assert offer["pack"]["content"] == {"value": "50", "unit": "kg"}
    assert offer["pack"]["confirmed"] is False
    assert offer["matched_product_id"] is None
    assert offer["supplier_id"] == ws["supplier_id"]


def test_typed_prices_give_one_offer_per_price(
    client: TestClient,
    ws: dict[str, Any],  # noqa: F811
) -> None:
    doc_id = imported(client, ws, LIST_2)
    keys = {o["price_field_key"] for o in offers_of(client, ws, doc_id)}
    assert keys == {"price_wholesale", "price_retail"}


def test_matching_decisions_and_candidates(
    client: TestClient,
    ws: dict[str, Any],  # noqa: F811
) -> None:
    doc_id = imported(client, ws, LIST_1)
    offers = offers_of(client, ws, doc_id)
    offer = cement(offers)
    product = client.post(f"{API}/offers/{offer['id']}/product", json={}).json()
    assert product["canonical_name"] == offer["name"]
    assert product["identifiers"] == []  # no SKU column in list 1
    matched = client.get(f"{API}/offers/{offer['id']}").json()
    assert matched["matched_product_id"] == product["id"]
    assert matched["decisions"][0]["actor"].startswith("user:")

    # A 25 kg bag of the "same" cement elsewhere would be blocked; here: the М400 offer.
    m400 = next(o for o in offers if o["name"].startswith("Цемент М400"))
    cands = client.get(f"{API}/offers/{m400['id']}/candidates").json()
    assert cands["auto_link_enabled"] is False
    blocked = next(c for c in cands["candidates"] if c["product_id"] == product["id"])
    assert blocked["blocked"] is True
    assert any(f["code"] == "ATTRIBUTE_CONFLICT" for f in blocked["features"])
    refused = client.post(
        f"{API}/offers/{m400['id']}/match-decisions",
        json={"decision": "accept", "product_id": product["id"]},
    )
    assert refused.status_code == 422
    assert "причины" in refused.json()["error"]["message"]

    revoke = client.post(
        f"{API}/offers/{offer['id']}/match-decisions",
        json={"decision": "revoke", "product_id": product["id"], "reason": "проверка"},
    )
    assert revoke.status_code == 201
    state = revoke.json()
    assert state["matched_product_id"] is None
    assert state["decisions"][1]["supersedes_id"] == state["decisions"][0]["id"]


def test_comparison_landed_cost_with_unknowns_policy_fx_and_tax(
    client: TestClient,
    ws: dict[str, Any],  # noqa: F811
) -> None:
    doc_id = imported(client, ws, LIST_1)
    offer = cement(offers_of(client, ws, doc_id))
    product = client.post(
        f"{API}/offers/{offer['id']}/product", json={"base_unit_code": "kg"}
    ).json()

    # Extend the policy with an own component type.
    policy = client.get(f"{API}/workspaces/{ws['id']}/cost-policy").json()
    assert policy["version"] == 0
    policy["types"].insert(
        2,
        {"code": "unloading", "label": "Разгрузка", "category": "charge", "basis": "per_unit"},
    )
    saved = client.put(
        f"{API}/workspaces/{ws['id']}/cost-policy",
        json={"name": policy["name"], "types": policy["types"], "expected_version": 0},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    stale = client.put(
        f"{API}/workspaces/{ws['id']}/cost-policy",
        json={"name": "x", "types": policy["types"], "expected_version": 0},
    )
    assert stale.status_code == 409
    no_tax_default = client.put(
        f"{API}/workspaces/{ws['id']}/cost-policy",
        json={
            "name": "x",
            "types": [
                {
                    "code": "vat",
                    "label": "НДС",
                    "category": "tax",
                    "basis": "percent",
                    "default_value": "20",
                }
            ],
            "expected_version": 1,
        },
    )
    assert no_tax_default.status_code == 422

    comparison = client.post(
        f"{API}/comparisons",
        json={
            "workspace_id": ws["id"],
            "name": "Цемент на объект",
            "target_currency": "RUB",
            "as_of": "2026-11-13",
        },
    ).json()
    with_item = client.post(
        f"{API}/comparisons/{comparison['id']}/items",
        json={"product_id": product["id"], "required_quantity": {"value": "1000", "unit": "kg"}},
    ).json()
    item_id = with_item["items"][0]["id"]

    # The offer is matched; its pack (50 kg) comes from the name and is not confirmed yet.
    attached = client.post(
        f"{API}/comparison-items/{item_id}/offers",
        json={"offer_id": offer["id"], "components": [], "price_includes_vat": False},
    )
    assert attached.status_code == 201, attached.text
    calc = client.post(f"{API}/comparisons/{comparison['id']}/calculate").json()
    snap = calc["items"][0]["offers"][0]["snapshot"]
    assert snap["total"] is None  # delivery is required and unknown — not zero
    assert snap["known_subtotal"] == {"amount": "10800.00", "currency": "RUB"}
    assert "delivery" in snap["unknown_components"]
    codes = {w["code"] for w in snap["warnings"]}
    assert {"UNKNOWN_COMPONENT", "CONVERSION_UNCONFIRMED", "PRICE_UNCONFIRMED"} <= codes
    delivery_row = next(c for c in snap["components"] if c["code"] == "delivery")
    assert delivery_row["amount"] is None

    # Confirm the pack, enter delivery in USD, unloading and VAT from a verified source.
    current = client.get(f"{API}/offers/{offer['id']}")
    confirmed = client.put(
        f"{API}/offers/{offer['id']}/pack",
        json={"content": {"value": "50", "unit": "kg"}},
        headers={"If-Match": current.headers["ETag"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["pack"]["confirmed"] is True
    client.post(
        f"{API}/workspaces/{ws['id']}/fx-rates",
        json={
            "base": "USD",
            "quote": "RUB",
            "rate": "92.50",
            "effective_date": "2026-11-12",
            "source": "ЦБ РФ",
        },
    )
    source = client.post(
        f"{API}/workspaces/{ws['id']}/tax-rate-sources",
        json={
            "name": "Ставки закупок",
            "reference": "Приказ № 12, проверено бухгалтерией",
            "verified_by": "Бухгалтер",
            "verified_at": "2026-01-10T09:00:00Z",
            "rates": [{"component_code": "vat", "rate": "20", "valid_from": "2026-01-01"}],
        },
    )
    assert source.status_code == 201, source.text
    co_id = calc["items"][0]["offers"][0]["id"]
    terms = client.put(
        f"{API}/comparison-offers/{co_id}/terms",
        json={
            "components": [
                {"code": "delivery", "value": "40", "currency": "USD", "source": "счёт"},
                {"code": "unloading", "value": "15", "currency": "RUB"},
            ],
            "price_includes_vat": False,
        },
    )
    assert terms.status_code == 200, terms.text
    final = client.post(f"{API}/comparisons/{comparison['id']}/calculate").json()
    snap2 = final["items"][0]["offers"][0]["snapshot"]
    amounts = {c["code"]: c["amount"] for c in snap2["components"]}
    assert amounts == {
        "goods": "10800.00",
        "delivery": "3700.00",
        "unloading": "300.00",
        "vat": "2960.00",  # 20% of 14800.00 (verified source)
    }
    assert snap2["total"] == {"amount": "17760.00", "currency": "RUB"}
    assert snap2["unit_cost"] == {"amount": "17.7600", "currency": "RUB"}
    # Everything is known, but the extracted price itself has not been reviewed yet.
    assert {w["code"] for w in snap2["warnings"]} == {
        "PRICE_UNCONFIRMED",
        "VAT_RECOVERY_NOT_ASSUMED",
    }
    assert snap2["complete"] is False
    assert snap2["supersedes_id"] == snap["id"]
    vat_row = next(c for c in snap2["components"] if c["code"] == "vat")
    assert vat_row["origin"] == "verified_source"

    detail = client.get(f"{API}/cost-snapshots/{snap2['id']}").json()
    assert detail["input"]["fx_used"][0]["source"] == "ЦБ РФ"
    assert detail["input"]["policy"]["types"][2]["code"] == "unloading"


def test_offer_must_be_matched_and_convertible(
    client: TestClient,
    ws: dict[str, Any],  # noqa: F811
) -> None:
    doc_id = imported(client, ws, LIST_1)
    offers = offers_of(client, ws, doc_id)
    offer, other = cement(offers), next(o for o in offers if o["name"].startswith("Песок"))
    product = client.post(f"{API}/offers/{offer['id']}/product", json={}).json()
    comparison = client.post(
        f"{API}/comparisons",
        json={
            "workspace_id": ws["id"],
            "name": "C",
            "target_currency": "RUB",
            "as_of": "2026-11-13",
        },
    ).json()
    item = client.post(
        f"{API}/comparisons/{comparison['id']}/items",
        json={"product_id": product["id"], "required_quantity": {"value": "10", "unit": "pc"}},
    ).json()["items"][0]
    unmatched = client.post(
        f"{API}/comparison-items/{item['id']}/offers", json={"offer_id": other["id"]}
    )
    assert unmatched.status_code == 422
    assert "не сопоставлено" in unmatched.json()["error"]["message"]
    wrong_unit = client.post(
        f"{API}/comparisons/{comparison['id']}/items",
        json={"product_id": product["id"], "required_quantity": {"value": "10", "unit": "kg"}},
    )
    assert wrong_unit.status_code == 422
