"""Unit tests: content sniffing, immutable store, CSV/XLSX adapters and price-line rules."""

from __future__ import annotations

import io
import os
import stat
import zipfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from pricepilot.application.intake import IncomingFile
from pricepilot.dip.adapters.csv_adapter import CsvParser
from pricepilot.dip.adapters.xlsx_adapter import XlsxParser
from pricepilot.dip.limits import DEFAULT_LIMITS, Limits
from pricepilot.dip.model import FileKind, ParseError
from pricepilot.dip.sniff import classify
from pricepilot.domain.price_lines import (
    ExtractionContext,
    ReviewReason,
    Role,
    currency_from_header,
    extract_price_lines,
    read_number,
    role_of,
    unit_from_text,
)
from pricepilot.infrastructure.storage.file_store import (
    LocalOriginalStore,
    StorageIntegrityError,
    UploadTooLargeError,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "price-lists"
LIST_1 = FIXTURES / "Прайс_1_obshchestroitelnye.xlsx"
LIST_2 = FIXTURES / "Прайс_2_otdelochnye.xlsx"
LIST_3 = FIXTURES / "Прайс_3_krovlya_pilomaterialy.xlsx"
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
CTX = ExtractionContext(fallback_currency="RUB")


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def xlsx_bytes(rows: Sequence[Sequence[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- Sniffing -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "ext", "kind"),
    [
        ("Прайс_1_obshchestroitelnye.xlsx", "xlsx", FileKind.XLSX),
        ("Прайс_1_obshchestroitelnye.docx", "docx", FileKind.DOCX),
        ("Прайс_1_obshchestroitelnye.pdf", "pdf", FileKind.PDF),
        ("Прайс_1_obshchestroitelnye.jpg", "jpg", FileKind.JPEG),
        ("Прайс_1_obshchestroitelnye.png", "png", FileKind.PNG),
    ],
)
def test_sniff_real_fixtures(fixture: str, ext: str, kind: FileKind) -> None:
    result = classify(FIXTURES / fixture, ext, DEFAULT_LIMITS)
    assert (result.kind, result.problem) == (kind, None)


@pytest.mark.parametrize(
    ("source", "ext"),
    [
        ("Прайс_1_obshchestroitelnye.pdf", "xlsx"),  # PDF pretending to be XLSX
        ("Прайс_1_obshchestroitelnye.xlsx", "pdf"),
        ("Прайс_1_obshchestroitelnye.docx", "xlsx"),  # both ZIP, different content
        ("Прайс_1_obshchestroitelnye.jpg", "csv"),
    ],
)
def test_sniff_detects_mime_spoofing(source: str, ext: str) -> None:
    result = classify(FIXTURES / source, ext, DEFAULT_LIMITS)
    assert result.problem == "INVALID_CONTENT"


def test_sniff_problem_cases(tmp_path: Path) -> None:
    assert classify(_write(tmp_path, "e.csv", b""), "csv", DEFAULT_LIMITS).problem == (
        "INVALID_CONTENT"
    )
    doc = classify(_write(tmp_path, "old.doc", OLE2 + b"\0" * 600), "doc", DEFAULT_LIMITS)
    assert (doc.kind, doc.problem) == (FileKind.DOC, "UNSUPPORTED_FORMAT")
    xls = classify(_write(tmp_path, "old.xls", OLE2 + b"\0" * 600), "xls", DEFAULT_LIMITS)
    assert (xls.kind, xls.problem) == (FileKind.XLS, None)
    encrypted = OLE2 + b"\0" * 100 + "EncryptedPackage".encode("utf-16-le")
    enc = classify(_write(tmp_path, "secret.xlsx", encrypted), "xlsx", DEFAULT_LIMITS)
    assert enc.problem == "PASSWORD_REQUIRED"
    exe = classify(_write(tmp_path, "a.exe", b"MZ\x90\x00binary"), "exe", DEFAULT_LIMITS)
    assert exe.problem == "UNSUPPORTED_FORMAT"
    truncated = LIST_1.read_bytes()[:2000]
    broken = classify(_write(tmp_path, "broken.xlsx", truncated), "xlsx", DEFAULT_LIMITS)
    assert broken.problem == "INVALID_CONTENT"


def test_sniff_zip_bomb_and_encrypted_entries(tmp_path: Path) -> None:
    bomb = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sheet.xml", b"\0" * 2_000_000)  # ratio ≫ limit
    assert classify(bomb, "xlsx", Limits(max_zip_ratio=50)).problem == "LIMIT_EXCEEDED"
    assert classify(bomb, "xlsx", Limits(max_zip_uncompressed_bytes=1000)).problem == (
        "LIMIT_EXCEEDED"
    )

    # zipfile cannot write encrypted entries; set the "encrypted" flag bit in the local header
    # (offset 6) and the central directory record (offset 8), as a real encrypted ZIP has.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "x")
    raw = bytearray(buf.getvalue())
    raw[6] |= 0x1
    raw[raw.find(b"PK\x01\x02") + 8] |= 0x1
    locked = _write(tmp_path, "locked.xlsx", bytes(raw))
    assert classify(locked, "xlsx", DEFAULT_LIMITS).problem == "PASSWORD_REQUIRED"


# --- Immutable store ----------------------------------------------------------------------


def _upload(store: LocalOriginalStore, data: bytes) -> IncomingFile:
    with store.begin_upload() as writer:
        writer.write(data)
        return writer.finish()


def test_store_commit_is_content_addressed_read_only_and_deduplicated(tmp_path: Path) -> None:
    store = LocalOriginalStore(tmp_path, max_bytes=1000)
    first = _upload(store, b"price;list")
    key = store.commit(first)
    assert key == f"originals/{first.sha256[:2]}/{first.sha256[2:4]}/{first.sha256}"
    path = tmp_path / key
    assert not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    second = _upload(store, b"price;list")
    assert store.commit(second) == key
    assert not second.path.exists()  # duplicate upload removed, original untouched
    assert store.read(key) == b"price;list"
    assert list((tmp_path / "incoming").iterdir()) == []


def test_store_detects_tampering_and_rejects_bad_keys(tmp_path: Path) -> None:
    store = LocalOriginalStore(tmp_path, max_bytes=1000)
    key = store.commit(_upload(store, b"original"))
    path = tmp_path / key
    os.chmod(path, 0o644)
    path.write_bytes(b"tampered")
    with pytest.raises(StorageIntegrityError):
        store.read(key)
    for bad in ("../../etc/passwd", "originals/aa/bb/../../x", f"{key}/x"):
        with pytest.raises(StorageIntegrityError):
            store.read(bad)


def test_store_limits_upload_and_cleans_partials(tmp_path: Path) -> None:
    store = LocalOriginalStore(tmp_path, max_bytes=10)
    with pytest.raises(UploadTooLargeError), store.begin_upload() as writer:
        writer.write(b"x" * 11)
    assert list((tmp_path / "incoming").iterdir()) == []
    (tmp_path / "incoming" / "crash.part").write_bytes(b"partial")
    assert store.clean_incoming() == 1


# --- Price-line rules ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "problem"),
    [
        ("540", "540", None),
        ("18,5", "18.5", None),
        ("1 234,50", "1234.50", None),
        ("1 234,50 руб.", "1234.50", None),
        ("1,234.50", "1234.50", None),
        ("1.234,50", "1234.50", None),
        ("1,234,567", "1234567", None),
        ("0,0005", "0.0005", None),
        ("1,234", None, ReviewReason.AMBIGUOUS_NUMBER),
        ("1.234", None, ReviewReason.AMBIGUOUS_NUMBER),
        ("-5", None, ReviewReason.INVALID_NUMBER),
        ("по запросу", None, ReviewReason.INVALID_NUMBER),
        ("", None, ReviewReason.MISSING_PRICE),
    ],
)
def test_read_number_never_guesses(text: str, value: str | None, problem: object) -> None:
    reading = read_number(text)
    assert reading.value == (Decimal(value) if value else None)
    assert reading.problem == problem


@pytest.mark.parametrize(
    ("raw", "code"),
    [("шт.", "pc"), ("м³", "m3"), ("м2", "m2"), ("куб. м", "m3"), ("Мешок", "bag"), ("кг", "kg")],
)
def test_unit_aliases(raw: str, code: str) -> None:
    assert unit_from_text(raw) == code


def test_unknown_unit_and_header_roles() -> None:
    assert unit_from_text("палета") is None
    assert role_of("Наименование") is Role.NAME
    assert role_of("Ед. изм.") is Role.UNIT
    assert role_of("Опт, руб.") is Role.PRICE
    assert role_of("Код") is Role.SKU
    assert role_of("№") is None
    assert currency_from_header("Цена, руб.") == "RUB"
    assert currency_from_header("Цена") is None


# --- XLSX adapter on the synthetic corpus --------------------------------------------------

EXPECTED_LINES = {"1": 39, "2": 41, "3": 39}  # counted independently from the source cells


@pytest.mark.parametrize(
    "path", sorted(FIXTURES.rglob("*.xlsx")), ids=lambda p: p.relative_to(FIXTURES).as_posix()
)
def test_xlsx_fixture_line_counts(path: Path) -> None:
    lines = extract_price_lines(XlsxParser().parse(path.read_bytes(), DEFAULT_LIMITS), CTX)
    list_no = path.name.split("_")[1]
    assert len(lines) == EXPECTED_LINES[list_no]
    review = {r for line in lines for r in line.review_reasons}
    assert review == ({"MULTIPLE_PRICE_COLUMNS"} if list_no == "2" else set())


def test_xlsx_provenance_and_values_list_1() -> None:
    lines = extract_price_lines(XlsxParser().parse(LIST_1.read_bytes(), DEFAULT_LIMITS), CTX)
    first = {f.key: f for f in lines[0].fields}
    assert first["name"].raw_value == "Цемент М500 Д0, мешок 50 кг"
    assert first["name"].location.cell == "B7"
    assert first["section"].normalized == {"value": "Цемент, песок, щебень"}
    assert first["unit"].normalized == {"value": "pc"}
    assert first["price"].normalized == {"amount": "540", "currency": "RUB"}
    assert first["price"].header == "Цена, руб."
    assert lines[0].location.row == 7
    brick = next(line for line in lines if line.location.row == 17)
    price = next(f for f in brick.fields if f.key == "price")
    assert price.normalized == {"amount": "18.5", "currency": "RUB"}  # float cell, exact text


def test_xlsx_multiple_price_columns_kept_for_choice() -> None:
    promo = FIXTURES / "Цены_акция" / "Прайс_2_otdelochnye_акция.xlsx"
    lines = extract_price_lines(XlsxParser().parse(promo.read_bytes(), DEFAULT_LIMITS), CTX)
    plaster = {f.key: f for f in lines[0].fields}
    assert plaster["sku"].raw_value == "СМ-101"
    assert plaster["section"].normalized == {"value": "Сухие смеси"}
    assert plaster["price_1"].normalized == {"amount": "400", "currency": "RUB"}
    assert plaster["price_1"].header == "Опт, руб."
    assert plaster["price_2"].normalized == {"amount": "455", "currency": "RUB"}
    assert plaster["price_2"].header == "Розница, руб."
    assert "price" not in plaster  # nothing is pre-selected
    assert lines[0].review_reasons == ("MULTIPLE_PRICE_COLUMNS",)


def test_xlsx_catalog_layout_with_characteristics() -> None:
    lines = extract_price_lines(XlsxParser().parse(LIST_3.read_bytes(), DEFAULT_LIMITS), CTX)
    board = {f.key: f for f in lines[0].fields}
    assert board["characteristics"].raw_value == "25×150×6000, сорт 2"
    assert board["section"].normalized == {"value": "Пиломатериалы"}
    assert board["unit"].normalized == {"value": "m3"}


def test_xlsx_formula_is_not_evaluated() -> None:
    data = xlsx_bytes([["Наименование", "Ед.", "Цена, руб."], ["Товар", "шт", "=2*500"]])
    lines = extract_price_lines(XlsxParser().parse(data, DEFAULT_LIMITS), CTX)
    # openpyxl-written files carry no cached value: we report a missing price, never compute it.
    assert lines[0].review_reasons == ("MISSING_PRICE",)


def test_xlsx_currency_assumption_and_unknown_unit_flagged() -> None:
    data = xlsx_bytes([["Наименование", "Ед.", "Цена"], ["Товар", "палета", 10]])
    lines = extract_price_lines(XlsxParser().parse(data, DEFAULT_LIMITS), ExtractionContext("KZT"))
    fields = {f.key: f for f in lines[0].fields}
    assert fields["price"].normalized == {"amount": "10", "currency": "KZT"}
    assert set(lines[0].review_reasons) == {"ASSUMED_CURRENCY", "UNKNOWN_UNIT"}
    assert fields["unit"].normalized is None
    assert fields["unit"].raw_value == "палета"


def test_xlsx_limits_and_damage() -> None:
    with pytest.raises(ParseError) as damaged:
        XlsxParser().parse(LIST_1.read_bytes()[:3000], DEFAULT_LIMITS)
    assert damaged.value.code == "INVALID_CONTENT"
    many = xlsx_bytes([["Наименование", "Цена"]] + [[f"T{i}", i] for i in range(30)])
    with pytest.raises(ParseError) as too_many:
        XlsxParser().parse(many, Limits(max_rows=10))
    assert too_many.value.code == "LIMIT_EXCEEDED"
    with pytest.raises(ParseError) as empty:
        XlsxParser().parse(xlsx_bytes([]), DEFAULT_LIMITS)
    assert empty.value.code == "NO_TABLE_FOUND"


# --- CSV adapter ----------------------------------------------------------------------------


def test_csv_semicolon_utf8_with_locale_numbers() -> None:
    data = (
        "Наименование;Ед. изм.;Цена, руб.\nЦемент М500;шт;1 234,50\nПесок;м³;1,234\nЩебень;м3;650\n"
    ).encode()
    doc = CsvParser().parse(data, DEFAULT_LIMITS)
    assert doc.diagnostics == {"encoding": "utf-8", "delimiter": ";"}
    lines = extract_price_lines(doc, CTX)
    prices = [next(f for f in line.fields if f.key == "price") for line in lines]
    assert prices[0].normalized == {"amount": "1234.50", "currency": "RUB"}
    assert prices[0].location.cell == "C2"
    assert prices[1].normalized is None  # "1,234" is ambiguous → review
    assert lines[1].review_reasons == ("AMBIGUOUS_NUMBER",)
    assert prices[1].raw_value == "1,234"
    assert lines[2].review_reasons == ()


def test_csv_cp1251_and_quoted_commas() -> None:
    data = 'Наименование,Цена руб\n"Клей, плиточный",310\n'.encode("cp1251")
    doc = CsvParser().parse(data, DEFAULT_LIMITS)
    assert doc.diagnostics["encoding"] == "cp1251"
    lines = extract_price_lines(doc, CTX)
    assert lines[0].fields[0].raw_value == "Клей, плиточный"
    assert "MISSING_UNIT" in lines[0].review_reasons


def test_csv_rejects_binary_and_limits() -> None:
    with pytest.raises(ParseError) as binary:
        CsvParser().parse(b"a;b\x00c", DEFAULT_LIMITS)
    assert binary.value.code == "INVALID_CONTENT"
    with pytest.raises(ParseError) as rows:
        CsvParser().parse(b"a;b\n" * 20, Limits(max_rows=10))
    assert rows.value.code == "LIMIT_EXCEEDED"
    with pytest.raises(ParseError) as cell:
        CsvParser().parse(b"a;" + b"x" * 200 + b"\n", Limits(max_cell_chars=100))
    assert cell.value.code == "LIMIT_EXCEEDED"
    with pytest.raises(ParseError) as blank:
        CsvParser().parse(b"\n\n  \n", DEFAULT_LIMITS)
    assert blank.value.code == "NO_TABLE_FOUND"


def test_csv_without_product_header_yields_no_lines() -> None:
    doc = CsvParser().parse("просто;текст\nбез;заголовков\n".encode(), DEFAULT_LIMITS)
    assert extract_price_lines(doc, CTX) == []
