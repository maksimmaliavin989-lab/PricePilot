"""TASK_003 acceptance through the HTTP API: correct, empty, damaged, oversized and
MIME-spoofed files; dedup; partial failure isolation; restart recovery; offline."""

from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from pricepilot.api.app import build_runtime
from pricepilot.dip.adapters.registry import PIPELINE_VERSION, ParserRegistry
from pricepilot.dip.limits import DEFAULT_LIMITS
from pricepilot.domain.documents import ImportSource, RunStatus
from tests.conftest import UPLOAD_LIMIT

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "price-lists"
LIST_1 = FIXTURES / "Прайс_1_obshchestroitelnye.xlsx"
LIST_2 = FIXTURES / "Прайс_2_otdelochnye.xlsx"
PDF = FIXTURES / "Прайс_1_obshchestroitelnye.pdf"
JPG = FIXTURES / "Прайс_1_obshchestroitelnye.jpg"
SCAN_PDF = FIXTURES / "Прайс_1_obshchestroitelnye_скан.pdf"
DOCX = FIXTURES / "Прайс_2_otdelochnye.docx"
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64


@pytest.fixture
def ws(client: TestClient) -> dict[str, Any]:
    workspace: dict[str, Any] = client.post(
        "/api/v1/workspaces",
        json={"name": "WS", "locale": "ru-RU", "base_currency": "RUB", "country_code": "RU"},
    ).json()
    supplier = client.post(
        "/api/v1/suppliers",
        json={
            "workspace_id": workspace["id"],
            "name": "ООО «СтройМатериалы Плюс»",
            "country_code": "RU",
            "default_currency": "RUB",
        },
    ).json()
    workspace["supplier_id"] = supplier["id"]
    return workspace


def upload(client: TestClient, ws: dict[str, Any], name: str, data: bytes, **params: str) -> Any:
    return client.post(
        "/api/v1/documents",
        params={"workspace_id": ws["id"], "supplier_id": ws["supplier_id"], **params},
        content=data,
        headers={"X-File-Name": quote(name), "Content-Type": "application/octet-stream"},
    )


def originals(data_dir: Path) -> list[Path]:
    return [p for p in (data_dir / "originals").rglob("*") if p.is_file()]


def test_correct_xlsx_is_processed_with_provenance(
    client: TestClient, ws: dict[str, Any], data_dir: Path
) -> None:
    resp = upload(client, ws, LIST_1.name, LIST_1.read_bytes())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["error"] is None
    assert body["duplicate_of"] is None
    doc = body["document"]
    assert doc["original_name"] == LIST_1.name
    assert doc["detected_mime"].endswith("spreadsheetml.sheet")
    assert doc["status"] == "completed"
    run = body["run"]
    assert (run["status"], run["counts"]["lines"]) == ("completed", 39)
    assert run["pipeline_version"] == PIPELINE_VERSION

    lines = client.get(f"/api/v1/documents/{doc['id']}/lines", params={"limit": 1}).json()
    line = lines["items"][0]
    assert line["source"]["sheet"] == "Прайс-лист"
    assert line["review_reasons"] == []
    price = next(f for f in line["fields"] if f["field_key"] == "price")
    assert price["normalized_value"] == {"amount": "540", "currency": "RUB"}
    assert price["provenance"]["cell"] == "D7"
    assert price["provenance"]["header"] == "Цена, руб."
    assert price["provenance"]["extractor"] == "xlsx"
    assert price["provenance"]["localization"] == "exact"
    assert len(originals(data_dir)) == 1


def test_typed_price_columns_do_not_require_review(client: TestClient, ws: dict[str, Any]) -> None:
    body = upload(client, ws, LIST_2.name, LIST_2.read_bytes()).json()
    # опт/розница are two typed prices of the list, not a per-line ambiguity
    assert body["document"]["status"] == "completed"
    assert body["run"]["counts"] == {"lines": 41, "fields": 41 * 6, "lines_review_required": 0}
    line = client.get(f"/api/v1/documents/{body['document']['id']}/lines").json()["items"][0]
    prices = {
        f["field_key"]: f["normalized_value"] for f in line["fields"] if "price" in f["field_key"]
    }
    assert prices == {
        "price_wholesale": {"amount": "430", "currency": "RUB"},
        "price_retail": {"amount": "490", "currency": "RUB"},
    }


def test_same_bytes_are_deduplicated(
    client: TestClient, ws: dict[str, Any], data_dir: Path
) -> None:
    first = upload(client, ws, LIST_1.name, LIST_1.read_bytes()).json()
    again = upload(client, ws, "копия.xlsx", LIST_1.read_bytes())
    assert again.status_code == 200
    body = again.json()
    assert body["duplicate_of"] == first["document"]["id"]
    assert body["document"]["original_name"] == LIST_1.name
    assert body["run"] is None
    listed = client.get("/api/v1/documents", params={"workspace_id": ws["id"]}).json()
    assert len(listed["items"]) == 1
    run = client.get(f"/api/v1/processing-runs/{first['run']['id']}").json()
    assert run["counts"]["lines"] == 39  # no duplicated lines
    assert len(originals(data_dir)) == 1


def test_recognised_but_not_yet_supported_format_is_stored_not_faked(
    client: TestClient, ws: dict[str, Any], data_dir: Path
) -> None:
    body = upload(client, ws, "фото.heic", HEIC).json()
    assert body["error"]["code"] == "UNSUPPORTED_FORMAT"
    assert body["run"]["status"] == "failed"
    assert body["run"]["error"] == {"code": "UNSUPPORTED_FORMAT", "retryable": False}
    assert body["document"]["status"] == "failed"
    assert body["document"]["detected_mime"] == "image/heic"
    assert "HEIC" in body["error"]["message"]
    assert len(originals(data_dir)) == 1  # original preserved
    lines = client.get(f"/api/v1/documents/{body['document']['id']}/lines").json()
    assert lines["items"] == []
    retry = client.post(f"/api/v1/processing-runs/{body['run']['id']}/retry")
    assert retry.status_code == 409  # not offered as retryable
    again = client.post(f"/api/v1/documents/{body['document']['id']}/processing-runs")
    assert again.json()["error"]["code"] == "INVALID_STATE"


def test_legacy_doc_is_unsupported(client: TestClient, ws: dict[str, Any]) -> None:
    body = upload(client, ws, "старый.doc", OLE2 + b"\0" * 1000).json()
    assert body["document"]["status"] == "unsupported"
    assert body["run"] is None
    assert body["error"]["code"] == "UNSUPPORTED_FORMAT"
    assert "DOCX" in body["error"]["message"]


def test_mime_spoofed_file(client: TestClient, ws: dict[str, Any]) -> None:
    body = upload(client, ws, "прайс.xlsx", PDF.read_bytes()).json()
    assert body["error"]["code"] == "INVALID_CONTENT"
    assert body["run"]["error"]["code"] == "INVALID_CONTENT"
    assert body["document"]["status"] == "failed"
    assert body["document"]["detected_mime"] == "application/pdf"


def test_damaged_xlsx_keeps_original(
    client: TestClient, ws: dict[str, Any], data_dir: Path
) -> None:
    body = upload(client, ws, "битый.xlsx", LIST_1.read_bytes()[:4000]).json()
    assert body["run"]["status"] == "failed"
    assert body["run"]["error"]["code"] == "INVALID_CONTENT"
    assert len(originals(data_dir)) == 1


def test_empty_file_rejected_and_not_stored(
    client: TestClient, ws: dict[str, Any], data_dir: Path
) -> None:
    resp = upload(client, ws, "пустой.csv", b"")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_CONTENT"
    assert originals(data_dir) == []


def test_oversized_file_rejected_and_not_stored(
    client: TestClient, ws: dict[str, Any], data_dir: Path
) -> None:
    resp = upload(client, ws, "big.csv", b"a;1\n" * (UPLOAD_LIMIT // 4 + 1))
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "LIMIT_EXCEEDED"
    assert originals(data_dir) == []
    assert list((data_dir / "incoming").iterdir()) == []
    listed = client.get("/api/v1/documents", params={"workspace_id": ws["id"]}).json()
    assert listed["items"] == []


def test_csv_without_table_and_bad_names(client: TestClient, ws: dict[str, Any]) -> None:
    body = upload(client, ws, "заметки.csv", "просто текст\nбез таблицы\n".encode()).json()
    assert body["run"]["error"]["code"] == "NO_TABLE_FOUND"
    assert body["document"]["status"] == "failed"

    traversal = upload(client, ws, "../../etc/passwd.csv", b"x;1\n")
    assert traversal.status_code == 422
    missing = client.post(
        "/api/v1/documents",
        params={"workspace_id": "00000000-0000-4000-8000-000000000000"},
        content=b"x;1\n",
        headers={"X-File-Name": "a.csv"},
    )
    assert missing.status_code == 404


def test_partial_failure_does_not_stop_the_batch(client: TestClient, ws: dict[str, Any]) -> None:
    results = [
        upload(client, ws, "битый.xlsx", LIST_1.read_bytes()[:4000]).json(),
        upload(client, ws, "прайс.xlsx", PDF.read_bytes()).json(),
        upload(client, ws, LIST_2.name, LIST_2.read_bytes()).json(),
        upload(
            client,
            ws,
            "прайс.csv",
            "Наименование;Ед.;Цена, руб.\nКлей;шт;310\n".encode(),
        ).json(),
    ]
    assert [r["document"]["status"] for r in results] == [
        "failed",
        "failed",
        "completed",
        "completed",
    ]


def test_reprocess_is_idempotent(client: TestClient, ws: dict[str, Any]) -> None:
    body = upload(client, ws, LIST_1.name, LIST_1.read_bytes()).json()
    again = client.post(f"/api/v1/documents/{body['document']['id']}/processing-runs")
    assert again.status_code == 200
    assert again.json()["id"] == body["run"]["id"]


def test_logs_do_not_contain_document_content(
    client: TestClient, ws: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    upload(client, ws, LIST_1.name, LIST_1.read_bytes())
    upload(client, ws, "битый.xlsx", LIST_1.read_bytes()[:4000])
    text = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    for secret in ("Цемент", "540", "СТРОЙМАТЕРИАЛЫ", LIST_1.name):
        assert secret not in text


def test_import_works_without_network(
    client: TestClient, ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    body = upload(client, ws, LIST_1.name, LIST_1.read_bytes()).json()
    assert body["document"]["status"] == "completed"


# --- Worker-level behaviour ---------------------------------------------------------------


def test_restart_recovers_interrupted_run(engine: Engine, tmp_path: Path) -> None:
    # A first process registers the document and dies in the middle of parsing.
    runtime = build_runtime(engine, tmp_path / "files", inline_worker=True)
    svc = runtime.services
    ws = svc.workspaces.create(name="W", locale="ru", base_currency="RUB", country_code="RU")
    with runtime.store.begin_upload() as writer:
        writer.write(LIST_1.read_bytes())
        incoming = writer.finish()
    key = runtime.store.commit(incoming)
    doc = svc.documents.register(
        workspace_id=ws.id,
        supplier_id=None,
        original_name=LIST_1.name,
        detected_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extension="xlsx",
        size_bytes=incoming.size,
        sha256=incoming.sha256,
        storage_key=key,
        import_source=ImportSource.FILE_PICKER,
        supported=True,
    ).document
    run, _ = svc.processing.start(
        doc.id, PIPELINE_VERSION, ParserRegistry().config_hash(DEFAULT_LIMITS)
    )
    svc.processing.advance(run.id, RunStatus.VALIDATING)
    svc.processing.advance(run.id, RunStatus.PARSING)

    # The next start recovers it: failed(INTERRUPTED, retryable) → queued → processed.
    restarted = build_runtime(engine, tmp_path / "files", inline_worker=True)
    restarted.worker.start()
    final = restarted.services.processing.get(run.id)
    assert (final.run.status, final.run.attempt, final.counts.lines) == (
        RunStatus.COMPLETED,
        2,
        39,
    )


def test_cancelled_run_is_not_processed(engine: Engine, tmp_path: Path) -> None:
    runtime = build_runtime(engine, tmp_path / "files", inline_worker=True)
    svc = runtime.services
    ws = svc.workspaces.create(name="W", locale="ru", base_currency="RUB", country_code="RU")
    with runtime.store.begin_upload() as writer:
        writer.write(LIST_1.read_bytes())
        incoming = writer.finish()
    key = runtime.store.commit(incoming)
    doc = svc.documents.register(
        workspace_id=ws.id,
        supplier_id=None,
        original_name="a.xlsx",
        detected_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extension="xlsx",
        size_bytes=incoming.size,
        sha256=incoming.sha256,
        storage_key=key,
        import_source=ImportSource.FILE_PICKER,
        supported=True,
    ).document
    run, _ = svc.processing.start(doc.id, PIPELINE_VERSION, "e" * 64)
    svc.processing.cancel(run.id)
    runtime.worker.submit(run.id)
    view = svc.processing.get(run.id)
    assert (view.run.status, view.counts.lines) == (RunStatus.CANCELLED, 0)


def test_source_rows_window_matches_provenance(client: TestClient, ws: dict[str, Any]) -> None:
    doc = upload(client, ws, LIST_1.name, LIST_1.read_bytes()).json()["document"]
    url = f"/api/v1/documents/{doc['id']}/source-rows"
    window = client.get(url, params={"sheet": "Прайс-лист", "from": 5, "to": 7}).json()
    assert (window["sheet"], window["sheets"], window["total_rows"]) == (
        "Прайс-лист",
        ["Прайс-лист"],
        53,
    )
    assert [r["index"] for r in window["rows"]] == [5, 6, 7]  # header, section, first product
    header = window["rows"][0]["cells"]
    assert [c["text"] for c in header] == ["№", "Наименование", "Ед. изм.", "Цена, руб."]
    price = window["rows"][2]["cells"][3]
    assert (price["ref"], price["text"]) == ("D7", "540")  # same cell as the line provenance

    assert client.get(url, params={"sheet": "Нет", "from": 1, "to": 2}).status_code == 404
    too_wide = client.get(url, params={"from": 1, "to": 500})
    assert too_wide.status_code == 422
    reversed_range = client.get(url, params={"from": 9, "to": 3})
    assert reversed_range.status_code == 422


def test_source_rows_csv_and_unsupported(client: TestClient, ws: dict[str, Any]) -> None:
    csv_doc = upload(client, ws, "p.csv", "Наименование;Цена, руб.\nКлей;310\n".encode()).json()[
        "document"
    ]
    window = client.get(
        f"/api/v1/documents/{csv_doc['id']}/source-rows", params={"from": 1, "to": 2}
    ).json()
    assert window["sheet"] is None
    assert window["rows"][1]["cells"][1]["ref"] == "B2"

    heic_doc = upload(client, ws, "фото.heic", HEIC).json()["document"]
    resp = client.get(
        f"/api/v1/documents/{heic_doc['id']}/source-rows", params={"from": 1, "to": 2}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVALID_STATE"


def test_digital_pdf_and_docx_are_processed(client: TestClient, ws: dict[str, Any]) -> None:
    pdf = upload(client, ws, PDF.name, PDF.read_bytes()).json()
    assert pdf["error"] is None
    assert (pdf["document"]["status"], pdf["run"]["counts"]["lines"]) == ("completed", 39)
    line = client.get(f"/api/v1/documents/{pdf['document']['id']}/lines").json()["items"][0]
    price = next(f for f in line["fields"] if f["field_key"] == "price")
    assert price["normalized_value"] == {"amount": "540.00", "currency": "RUB"}
    assert price["provenance"]["page"] == 1
    assert price["provenance"]["cell"] is None
    assert price["provenance"]["region"] is not None
    assert price["provenance"]["extractor"] == "pdf"

    docx = upload(client, ws, DOCX.name, DOCX.read_bytes()).json()
    assert (docx["document"]["status"], docx["run"]["counts"]["lines"]) == ("completed", 41)


def test_scanned_pdf_and_image_are_recognised_locally(
    client: TestClient, ws: dict[str, Any], data_dir: Path
) -> None:
    body = upload(client, ws, SCAN_PDF.name, SCAN_PDF.read_bytes()).json()
    assert body["error"] is None
    assert body["run"]["counts"]["lines"] == 39  # 24 on page 1 + 15 on page 2
    assert body["document"]["status"] in {"completed", "review_required"}
    assert len(originals(data_dir)) == 1
    lines = client.get(f"/api/v1/documents/{body['document']['id']}/lines").json()["items"]
    price = next(f for f in lines[0]["fields"] if f["field_key"] == "price")
    assert price["normalized_value"] == {"amount": "540.00", "currency": "RUB"}
    assert price["provenance"]["page"] == 1
    assert price["provenance"]["region"] is not None
    assert price["provenance"]["method"] == "ocr"
    assert 0 < price["confidence"] <= 1
    assert {line["fields"][0]["provenance"]["page"] for line in lines} == {1, 2}

    jpg = upload(client, ws, JPG.name, JPG.read_bytes()).json()
    assert jpg["error"] is None
    assert jpg["run"]["counts"]["lines"] == 39
    window = client.get(
        f"/api/v1/documents/{jpg['document']['id']}/source-rows", params={"from": 1, "to": 8}
    ).json()
    assert any(c["text"] == "540,00" for r in window["rows"] for c in r["cells"])


def test_pdf_source_rows_carry_pages(client: TestClient, ws: dict[str, Any]) -> None:
    doc = upload(client, ws, PDF.name, PDF.read_bytes()).json()["document"]
    window = client.get(
        f"/api/v1/documents/{doc['id']}/source-rows", params={"from": 1, "to": 8}
    ).json()
    assert window["sheet"] is None
    assert window["rows"][5]["page"] == 1
    cell = window["rows"][5]["cells"][-1]
    assert cell["region"] is not None
    assert cell["text"] == "540,00"
