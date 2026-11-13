"""DOCX, digital PDF and legacy XLS adapters (ported from the pricepilot-web prototype)."""

from __future__ import annotations

import io
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest
import xlwt

from pricepilot.dip.adapters.docx_adapter import DocxParser
from pricepilot.dip.adapters.pdf_adapter import PdfParser
from pricepilot.dip.adapters.xls_adapter import XlsParser
from pricepilot.dip.adapters.xlsx_adapter import XlsxParser
from pricepilot.dip.limits import DEFAULT_LIMITS, Limits
from pricepilot.dip.model import ParseError, TableDocument
from pricepilot.domain.extraction import Localization, NewLine
from pricepilot.domain.price_lines import ExtractionContext, extract_price_lines

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "price-lists"
CTX = ExtractionContext(fallback_currency="RUB")
EXPECTED = {"1": 39, "2": 41, "3": 39}
SLUGS = {"1": "obshchestroitelnye", "2": "otdelochnye", "3": "krovlya_pilomaterialy"}


def lines_of(doc: TableDocument) -> list[NewLine]:
    return extract_price_lines(doc, CTX)


def summary(lines: list[NewLine]) -> list[tuple[str, dict[str, Decimal]]]:
    """(name, {price key: amount}) per line — comparable across formats."""
    out = []
    for line in lines:
        fields = {f.key: f for f in line.fields}
        name = fields["name"].raw_value or ""
        prices = {
            k: Decimal(f.normalized["amount"])
            for k, f in fields.items()
            if k.startswith("price") and f.normalized
        }
        out.append((name, prices))
    return out


# --- DOCX ---------------------------------------------------------------------------------


@pytest.mark.parametrize("n", ["1", "2", "3"])
def test_docx_fixtures_match_xlsx(n: str) -> None:
    base = f"Прайс_{n}_{SLUGS[n]}"
    docx = lines_of(DocxParser().parse((FIXTURES / f"{base}.docx").read_bytes(), DEFAULT_LIMITS))
    xlsx = lines_of(XlsxParser().parse((FIXTURES / f"{base}.xlsx").read_bytes(), DEFAULT_LIMITS))
    assert len(docx) == EXPECTED[n]
    assert all(not line.review_reasons for line in docx)
    assert summary(docx) == summary(xlsx)
    price = next(f for f in docx[0].fields if f.key.startswith("price"))
    assert price.location.sheet == "Таблица 2"
    assert price.location.cell is not None


def test_docx_damaged_and_empty() -> None:
    data = (FIXTURES / "Прайс_1_obshchestroitelnye.docx").read_bytes()
    with pytest.raises(ParseError) as damaged:
        DocxParser().parse(data[:3000], DEFAULT_LIMITS)
    assert damaged.value.code == "INVALID_CONTENT"
    with pytest.raises(ParseError) as rows:
        DocxParser().parse(data, Limits(max_rows=5))
    assert rows.value.code == "LIMIT_EXCEEDED"


def test_docx_paragraph_fallback() -> None:
    import docx

    document = docx.Document()
    document.add_paragraph("Наименование\tЕд.\tЦена, руб.")
    document.add_paragraph("Клей плиточный\tшт\t310")
    buf = io.BytesIO()
    document.save(buf)
    lines = lines_of(DocxParser().parse(buf.getvalue(), DEFAULT_LIMITS))
    assert len(lines) == 1
    fields = {f.key: f for f in lines[0].fields}
    assert fields["price"].normalized == {"amount": "310", "currency": "RUB"}
    assert fields["price"].location.sheet == "Текст"


# --- digital PDF ----------------------------------------------------------------------------

PDFS = sorted(p for p in FIXTURES.rglob("*.pdf") if "скан" not in p.name)


@pytest.mark.parametrize("path", PDFS, ids=lambda p: p.relative_to(FIXTURES).as_posix())
def test_pdf_fixtures_match_xlsx_counts_and_values(path: Path) -> None:
    n = path.name.split("_")[1]
    pdf = lines_of(PdfParser().parse(path.read_bytes(), DEFAULT_LIMITS))
    xlsx = lines_of(XlsxParser().parse(path.with_suffix(".xlsx").read_bytes(), DEFAULT_LIMITS))
    assert len(pdf) == EXPECTED[n]
    assert all(not line.review_reasons for line in pdf)
    assert summary(pdf) == summary(xlsx)  # same names and the same prices as the XLSX


def test_pdf_two_price_columns_are_kept_apart() -> None:
    path = FIXTURES / "Цены_акция" / "Прайс_2_otdelochnye_акция.pdf"
    first = {
        f.key: f for f in lines_of(PdfParser().parse(path.read_bytes(), DEFAULT_LIMITS))[0].fields
    }
    assert first["price_wholesale"].normalized == {"amount": "400.00", "currency": "RUB"}
    assert first["price_wholesale"].header == "Опт, руб."
    assert first["price_retail"].normalized == {"amount": "455.00", "currency": "RUB"}
    assert first["price_retail"].header == "Розница, руб."


def test_pdf_provenance_is_page_and_region() -> None:
    path = FIXTURES / "Прайс_1_obshchestroitelnye.pdf"
    lines = lines_of(PdfParser().parse(path.read_bytes(), DEFAULT_LIMITS))
    price = next(f for f in lines[0].fields if f.key == "price")
    loc = price.location
    assert (loc.page, loc.cell, loc.sheet) == (1, None, None)
    assert loc.region is not None
    assert 0.5 < loc.region.x < 1
    assert 0 < loc.region.y < 0.5
    assert loc.localization is Localization.EXACT
    assert lines[-1].location.page == 2  # the table continues on page 2
    # The header repeated at the top of page 2 is not a product.
    assert all(f.raw_value != "Наименование" for line in lines for f in line.fields)


def test_pdf_damaged_and_limits() -> None:
    data = (FIXTURES / "Прайс_1_obshchestroitelnye.pdf").read_bytes()
    with pytest.raises(ParseError) as damaged:
        PdfParser().parse(data[:2000], DEFAULT_LIMITS)
    assert damaged.value.code == "INVALID_CONTENT"
    with pytest.raises(ParseError) as pages:
        PdfParser().parse(data, Limits(max_pages=1))
    assert pages.value.code == "LIMIT_EXCEEDED"


# --- legacy XLS -------------------------------------------------------------------------------


def xls_bytes(rows: Sequence[Sequence[object]], sheet: str = "Прайс") -> bytes:
    book = xlwt.Workbook(encoding="utf-8")
    ws = book.add_sheet(sheet)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            ws.write(r, c, value)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def test_xls_values_and_provenance() -> None:
    data = xls_bytes(
        [
            ["ООО «Поставщик»"],
            ["Артикул", "Наименование", "Ед.", "Опт, руб.", "Розница, руб."],
            ["Сухие смеси"],
            ["СМ-101", "Штукатурка гипсовая, мешок 30 кг", "шт.", 430, 490.5],
            ["СМ-102", "Шпатлёвка", "шт.", 0.1 + 0.2, 590],
        ]
    )
    doc = XlsParser().parse(data, DEFAULT_LIMITS)
    lines = lines_of(doc)
    assert len(lines) == 2
    first = {f.key: f for f in lines[0].fields}
    assert first["price_wholesale"].normalized == {"amount": "430", "currency": "RUB"}
    assert first["price_retail"].normalized == {"amount": "490.5", "currency": "RUB"}
    assert first["price_wholesale"].location.cell == "D4"
    assert first["price_wholesale"].location.sheet == "Прайс"
    assert first["section"].normalized == {"value": "Сухие смеси"}
    second = {f.key: f for f in lines[1].fields}
    assert second["price_wholesale"].normalized == {"amount": "0.3", "currency": "RUB"}


def test_xls_damaged_and_limits() -> None:
    data = xls_bytes([["Наименование", "Цена"]] + [[f"T{i}", i] for i in range(20)])
    with pytest.raises(ParseError) as damaged:
        XlsParser().parse(data[:600], DEFAULT_LIMITS)
    assert damaged.value.code == "INVALID_CONTENT"
    with pytest.raises(ParseError) as rows:
        XlsParser().parse(data, Limits(max_rows=5))
    assert rows.value.code == "LIMIT_EXCEEDED"
