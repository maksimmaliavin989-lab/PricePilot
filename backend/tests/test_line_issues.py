"""Review remarks tied to fields: the action that resolves each one, live resolution."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from pricepilot.domain.extraction import ExtractedField, ReviewStatus, ValueType
from pricepilot.domain.line_issues import IssueAction, line_issues
from tests.test_import_api import upload, ws  # noqa: F401 - fixture

NOW = datetime(2026, 11, 14, tzinfo=UTC)
LINE = uuid4()


def field(
    key: str, vt: ValueType, value: dict[str, Any] | None, status: str = "unreviewed"
) -> ExtractedField:
    return ExtractedField(
        id=uuid4(),
        line_id=LINE,
        field_key=key,
        value_type=vt,
        raw_value="x",
        normalized_value=value,
        confidence=None,
        provenance={},
        review_status=ReviewStatus(status),
        revision=1,
        reviewed_by=None,
        reviewed_at=None,
    )


NAME = field("name", ValueType.TEXT, {"value": "Клей"})
PRICE = field("price", ValueType.MONEY, {"amount": "310", "currency": "RUB"})


def test_missing_unit_is_not_resolved_by_confirming_the_name() -> None:
    accepted_name = replace(NAME, review_status=ReviewStatus.ACCEPTED)
    (issue,) = line_issues(["MISSING_UNIT"], [accepted_name, PRICE])
    assert (issue.field_key, issue.action, issue.field_ids, issue.resolved) == (
        "unit",
        IssueAction.ADD_VALUE,
        (),
        False,
    )
    assert "выберите единицу" in issue.hint
    added = field("unit", ValueType.UNIT, {"value": "pc"}, "corrected")
    (after,) = line_issues(["MISSING_UNIT"], [accepted_name, PRICE, added])
    assert after.resolved
    assert after.field_ids == (added.id,)


@pytest.mark.parametrize(
    ("code", "action"),
    [
        ("UNKNOWN_UNIT", IssueAction.FIX_VALUE),
        ("UNIT_CORRECTED", IssueAction.CONFIRM_OR_FIX),
        ("UNIT_AMBIGUOUS", IssueAction.CONFIRM_OR_FIX),
    ],
)
def test_unit_remarks_point_to_the_unit_field(code: str, action: IssueAction) -> None:
    unread = code == "UNKNOWN_UNIT"
    unit = field("unit", ValueType.UNIT, None if unread else {"value": "m"})
    (issue,) = line_issues([code], [NAME, unit, PRICE])
    assert (issue.field_key, issue.field_ids, issue.action, issue.resolved) == (
        "unit",
        (unit.id,),
        action,
        False,
    )
    # Accepting a value that could not be read does not resolve anything.
    if not unread:
        (ok,) = line_issues(
            [code], [NAME, replace(unit, review_status=ReviewStatus.ACCEPTED), PRICE]
        )
        assert ok.resolved
    fixed = replace(unit, normalized_value={"value": "m3"}, review_status=ReviewStatus.CORRECTED)
    (done,) = line_issues([code], [NAME, fixed, PRICE])
    assert done.resolved


def test_price_remarks() -> None:
    unread = field("price", ValueType.MONEY, None)
    (invalid,) = line_issues(["INVALID_NUMBER"], [NAME, unread])
    assert (invalid.action, invalid.field_ids, invalid.resolved) == (
        IssueAction.FIX_VALUE,
        (unread.id,),
        False,
    )
    fixed = replace(
        unread,
        normalized_value={"amount": "1234", "currency": "RUB"},
        review_status=ReviewStatus.CORRECTED,
    )
    assert line_issues(["INVALID_NUMBER"], [NAME, fixed])[0].resolved

    p1 = field("price_1", ValueType.MONEY, {"amount": "430", "currency": "RUB"})
    p2 = field("price_2", ValueType.MONEY, {"amount": "490", "currency": "RUB"})
    (choose,) = line_issues(["MULTIPLE_PRICE_COLUMNS"], [NAME, p1, p2])
    assert (choose.action, choose.resolved) == (IssueAction.CHOOSE_PRICE, False)
    chosen = [NAME, replace(p1, review_status=ReviewStatus.ACCEPTED), p2]
    assert line_issues(["MULTIPLE_PRICE_COLUMNS"], chosen)[0].resolved

    (missing,) = line_issues(["MISSING_PRICE"], [NAME])
    assert (missing.action, missing.field_ids) == (IssueAction.ADD_VALUE, ())

    (currency,) = line_issues(["ASSUMED_CURRENCY"], [NAME, PRICE])
    assert not currency.resolved
    assert line_issues(
        ["ASSUMED_CURRENCY"], [NAME, replace(PRICE, review_status=ReviewStatus.ACCEPTED)]
    )[0].resolved


def test_the_stored_reasons_stay_as_history() -> None:
    issues = line_issues(["UNKNOWN_UNIT", "ASSUMED_CURRENCY", "UNKNOWN_UNIT"], [NAME, PRICE])
    assert [i.code for i in issues] == ["UNKNOWN_UNIT", "ASSUMED_CURRENCY"]


# --- API -------------------------------------------------------------------------------------

CSV = "Наименование;Ед.;Цена, руб.\nКлей плиточный;;310\nКраска фасадная;ведро;500\n".encode()


def lines_of(client: TestClient, doc_id: str) -> list[dict[str, Any]]:
    return list(client.get(f"/api/v1/documents/{doc_id}/lines").json()["items"])


def test_remarks_follow_the_fields_through_the_api(
    client: TestClient,
    ws: dict[str, Any],  # noqa: F811
) -> None:
    doc_id = upload(client, ws, "прайс.csv", CSV).json()["document"]["id"]
    glue, paint = lines_of(client, doc_id)
    assert [i["code"] for i in glue["issues"]] == ["MISSING_UNIT"]
    assert glue["issues"][0]["action"] == "add_value"
    assert glue["open_issues"] == 1

    # Confirming the name does not resolve "no unit".
    name = next(f for f in glue["fields"] if f["field_key"] == "name")
    client.post(
        f"/api/v1/extracted-fields/{name['id']}/decision",
        json={"decision": "accept", "reason": None, "expected_revision": name["revision"]},
    )
    assert lines_of(client, doc_id)[0]["open_issues"] == 1

    # Adding the unit does; the reason stays in the history.
    added = client.post(
        f"/api/v1/extracted-lines/{glue['id']}/fields",
        json={"field_key": "unit", "value": {"value": "bag"}, "reason": "мешок по договору"},
    )
    assert added.status_code == 201, added.text
    line = added.json()
    assert line["open_issues"] == 0
    assert line["issues"][0]["resolved"] is True
    assert line["review_reasons"] == ["MISSING_UNIT"]
    unit = next(f for f in line["fields"] if f["field_key"] == "unit")
    assert unit["raw_value"] is None
    assert unit["review_status"] == "corrected"
    assert unit["provenance"]["method"] == "user"
    again = client.post(
        f"/api/v1/extracted-lines/{glue['id']}/fields",
        json={"field_key": "unit", "value": {"value": "pc"}},
    )
    assert again.status_code == 422
    history = client.get(f"/api/v1/extracted-fields/{unit['id']}/revisions").json()
    assert history[0]["reason"] == "мешок по договору"

    # "ведро" is not a known unit: correcting the unit field resolves the remark.
    assert [i["code"] for i in paint["issues"]] == ["UNKNOWN_UNIT"]
    paint_unit = next(f for f in paint["fields"] if f["field_key"] == "unit")
    assert paint["issues"][0]["field_ids"] == [paint_unit["id"]]
    client.patch(
        f"/api/v1/extracted-fields/{paint_unit['id']}",
        json={
            "value": {"value": "pc"},
            "reason": None,
            "expected_revision": paint_unit["revision"],
        },
    )
    assert client.get(f"/api/v1/extracted-lines/{paint['id']}").json()["open_issues"] == 0
