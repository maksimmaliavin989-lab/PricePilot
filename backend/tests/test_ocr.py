"""Local OCR (RapidOCR, bundled models): scanned PDF pages and images.

Accuracy on the synthetic fixtures is compared with the XLSX originals: prices must be read
exactly and every line that is not fully right must be either correct in price or flagged.
"""

from __future__ import annotations

import io
import socket
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from pricepilot.dip.adapters.image_adapter import ImageParser
from pricepilot.dip.adapters.pdf_adapter import PdfParser
from pricepilot.dip.adapters.xlsx_adapter import XlsxParser
from pricepilot.dip.layout import Box
from pricepilot.dip.limits import DEFAULT_LIMITS, Limits
from pricepilot.dip.model import ParseError, TableDocument
from pricepilot.dip.ocr.engine import MODELS_DIR, RapidOcrEngine
from pricepilot.domain.extraction import NewLine
from pricepilot.domain.price_lines import ExtractionContext, extract_price_lines

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "price-lists"
CTX = ExtractionContext(fallback_currency="RUB")
SLUGS = {"1": "obshchestroitelnye", "2": "otdelochnye", "3": "krovlya_pilomaterialy"}


def prices(lines: list[NewLine]) -> list[tuple[Decimal, ...]]:
    out = []
    for line in lines:
        values = sorted(
            (f for f in line.fields if f.key.startswith("price") and f.normalized),
            key=lambda f: f.key,
        )
        out.append(tuple(Decimal(f.normalized["amount"]) for f in values if f.normalized))
    return out


def gold(n: str) -> list[NewLine]:
    data = (FIXTURES / f"Прайс_{n}_{SLUGS[n]}.xlsx").read_bytes()
    return extract_price_lines(XlsxParser().parse(data, DEFAULT_LIMITS), CTX)


def png_bytes(image: Image.Image, fmt: str = "PNG", **kw: Any) -> bytes:
    buf = io.BytesIO()
    image.save(buf, fmt, **kw)
    return buf.getvalue()


# --- valid files ----------------------------------------------------------------------------


@pytest.mark.parametrize("ext", ["png", "jpg"])
@pytest.mark.parametrize("n", ["1", "2", "3"])
def test_rendered_price_list_images(n: str, ext: str) -> None:
    data = (FIXTURES / f"Прайс_{n}_{SLUGS[n]}.{ext}").read_bytes()
    doc = ImageParser().parse(data, DEFAULT_LIMITS)
    lines = extract_price_lines(doc, CTX)
    # Same products in the same order with every price read exactly (опт/розница apart).
    assert prices(lines) == prices(gold(n))
    assert doc.diagnostics["ocr_engine"].startswith("rapidocr")
    for line in lines:
        for field in line.fields:
            if field.key != "section":
                assert field.method == "ocr"
                assert field.confidence is not None
                assert 0.0 < field.confidence <= 1.0
                assert field.location.region is not None
                assert field.location.page == 1


def test_scanned_pdf_pages_are_recognised() -> None:
    data = (FIXTURES / "Прайс_1_obshchestroitelnye_скан.pdf").read_bytes()
    doc = PdfParser().parse(data, DEFAULT_LIMITS)
    assert doc.diagnostics["ocr_pages"] == "1, 2"
    lines = extract_price_lines(doc, CTX)
    assert len(lines) == 39  # the header repeated on page 2 is not a product
    assert prices(lines) == prices(gold("1"))
    assert {line.location.page for line in lines} == {1, 2}
    # Units that OCR may have misread are never silently accepted.
    for line in lines:
        unit = next((f for f in line.fields if f.key == "unit"), None)
        if unit is not None and unit.normalized == {"value": "m"}:
            assert "UNIT_AMBIGUOUS" in line.review_reasons


def test_scanned_page_photo_jpeg() -> None:
    data = (FIXTURES / "Прайс_1_obshchestroitelnye_скан_стр1.jpg").read_bytes()
    lines = extract_price_lines(ImageParser().parse(data, DEFAULT_LIMITS), CTX)
    assert prices(lines) == prices(gold("1"))[:24]


def test_ocr_runs_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("OCR tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    engine = RapidOcrEngine()  # fresh instance: model loading happens under the guard
    data = (FIXTURES / "Прайс_2_otdelochnye.jpg").read_bytes()
    doc = ImageParser(engine).parse(data, DEFAULT_LIMITS)
    assert len(extract_price_lines(doc, CTX)) >= 39


# --- damaged files and limits ---------------------------------------------------------------


class NoText:
    def recognize(self, image: np.ndarray[Any, Any]) -> list[Box]:
        return []


def test_damaged_and_empty_images() -> None:
    data = (FIXTURES / "Прайс_1_obshchestroitelnye.png").read_bytes()
    for broken in (data[:500], b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, b"\xff\xd8\xff" + b"x" * 50):
        with pytest.raises(ParseError) as damaged:
            ImageParser(NoText()).parse(broken, DEFAULT_LIMITS)
        assert damaged.value.code == "INVALID_CONTENT"
    blank = png_bytes(Image.new("RGB", (400, 300), "white"))
    with pytest.raises(ParseError) as empty:
        ImageParser().parse(blank, DEFAULT_LIMITS)
    assert empty.value.code == "NO_TABLE_FOUND"


def test_image_limits() -> None:
    big = png_bytes(Image.new("L", (3000, 3000), 255))
    with pytest.raises(ParseError) as pixels:
        ImageParser(NoText()).parse(big, Limits(max_image_pixels=1_000_000))
    assert pixels.value.code == "LIMIT_EXCEEDED"

    frames = [Image.new("L", (50, 50), 255) for _ in range(3)]
    tiff = png_bytes(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    with pytest.raises(ParseError) as pages:
        ImageParser(NoText()).parse(tiff, Limits(max_ocr_pages=2))
    assert pages.value.code == "LIMIT_EXCEEDED"

    scan = (FIXTURES / "Прайс_1_obshchestroitelnye_скан.pdf").read_bytes()
    with pytest.raises(ParseError) as scanned:
        PdfParser(NoText()).parse(scan, Limits(max_ocr_pages=1))
    assert scanned.value.code == "LIMIT_EXCEEDED"


def test_missing_models_fail_explicitly(tmp_path: Path) -> None:
    assert (MODELS_DIR / "NOTICE.md").is_file()
    data = (FIXTURES / "Прайс_1_obshchestroitelnye.png").read_bytes()
    with pytest.raises(ParseError) as missing:
        ImageParser(RapidOcrEngine(models_dir=tmp_path)).parse(data, DEFAULT_LIMITS)
    assert missing.value.code == "OCR_RESOURCE_MISSING"


def test_tiff_pages_become_pages() -> None:
    class OneWord:
        def recognize(self, image: np.ndarray[Any, Any]) -> list[Box]:
            return [Box("Текст", 5, 40, 5, 15, confidence=0.9)]

    frames = [Image.new("L", (100, 50), 255) for _ in range(2)]
    tiff = png_bytes(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    doc: TableDocument = ImageParser(OneWord()).parse(tiff, DEFAULT_LIMITS)
    rows = doc.sheets[0].rows
    assert [r.page for r in rows] == [1, 2]
    cell = rows[0].cells[0]
    assert cell.confidence == 0.9
    assert cell.region == (0.05, 0.1, 0.35, 0.2)
