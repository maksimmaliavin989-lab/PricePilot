"""Post-processing rules for recognised text (no OCR engine needed)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from pricepilot.dip.layout import Box, build_rows, group_lines, split_fused_words
from pricepilot.dip.limits import DEFAULT_LIMITS
from pricepilot.dip.model import Cell, Row, Sheet, TableDocument
from pricepilot.dip.ocr_text import clean_ocr_text
from pricepilot.domain.extraction import NewLine
from pricepilot.domain.price_lines import (
    ExtractionContext,
    extract_price_lines,
    infer_header,
    unit_from_ocr_text,
)

CTX = ExtractionContext(fallback_currency="RUB")


@pytest.mark.parametrize(
    ("raw", "text", "suspicious"),
    [
        ("M{3 }$", "M³", True),
        ("m{2}", "m²", True),
        ("m{ }$", "m?", True),
        ("M3 }$", "M3", True),
        ("Цемент М500", "Цемент М500", False),
    ],
)
def test_clean_ocr_text(raw: str, text: str, suspicious: bool) -> None:
    assert clean_ocr_text(raw) == (text, suspicious)


@pytest.mark.parametrize(
    ("raw", "code", "corrected"),
    [
        ("шт.", "pc", False),
        ("шT.", "pc", False),  # mixed Latin/Cyrillic look-alikes: same letters
        ("M3", "m3", False),
        ("yn.", "pack", True),
        ("Kr", "kg", True),
        ("мз", "m3", True),
        ("M^2", "m2", True),
        ("НИСТ", None, False),
        ("м", "m", False),
    ],
)
def test_unit_from_ocr_text(raw: str, code: str | None, corrected: bool) -> None:
    assert unit_from_ocr_text(raw) == (code, corrected)


def test_split_fused_words() -> None:
    parts = split_fused_words(Box("рубРозница,", 0, 110, 0, 10, confidence=0.8))
    assert [p.text for p in parts] == ["руб", "Розница,"]
    assert parts[0].x1 == pytest.approx(30)
    assert all(p.confidence == 0.8 for p in parts)
    assert split_fused_words(Box("Цемент", 0, 60, 0, 10)) == [Box("Цемент", 0, 60, 0, 10)]


def page_lines(
    rows: Sequence[Sequence[tuple[str, float]]], confidence: float | None = 0.9
) -> TableDocument:
    """Synthetic OCR page: each row is [(text, x0)], words 10 px per character."""
    boxes = [
        Box(text, x, x + 10 * len(text), 30 * i, 30 * i + 20, confidence=confidence)
        for i, row in enumerate(rows)
        for text, x in row
    ]
    built, _ = build_rows(group_lines(boxes, 1, 1000, 1000), DEFAULT_LIMITS)
    return TableDocument(sheets=(Sheet(name=None, rows=tuple(built)),))


def fields(line: NewLine) -> dict[str, str | None]:
    return {f.key: f.raw_value for f in line.fields}


def test_name_continuation_on_a_page_joins_the_name() -> None:
    doc = page_lines(
        [
            [("Наименование", 100), ("Ед.", 600), ("Цена", 800)],
            [
                ("Цемент", 100),
                ("М500,", 170),
                ("мешок", 400),
                ("50", 460),
                ("кг", 490),
                ("шт.", 600),
                ("540,00", 800),
            ],
        ]
    )
    (line,) = extract_price_lines(doc, CTX)
    assert fields(line)["name"] == "Цемент М500, мешок 50 кг"
    region = next(f for f in line.fields if f.key == "name").location.region
    assert region is not None
    assert region.x == pytest.approx(0.1)
    assert region.width > 0.4


def test_spreadsheet_cells_are_never_joined() -> None:
    sheet = Sheet(
        name="Лист1",
        rows=(
            Row(1, (Cell(1, 1, "Наименование"), Cell(1, 3, "Ед."), Cell(1, 4, "Цена"))),
            Row(
                2, (Cell(2, 1, "Цемент"), Cell(2, 2, "мешок"), Cell(2, 3, "шт"), Cell(2, 4, "540"))
            ),
        ),
    )
    (line,) = extract_price_lines(TableDocument(sheets=(sheet,)), CTX)
    assert fields(line)["name"] == "Цемент"


def test_ocr_units_are_confirmed_by_the_user() -> None:
    doc = page_lines(
        [
            [("Наименование", 100), ("Ед.", 600), ("Цена,", 800), ("руб.", 860)],
            [("Песок", 100), ("м", 600), ("650,00", 800)],
            [("Саморез", 100), ("yn.", 600), ("390,00", 800)],
            [("Цемент", 100), ("шт.", 600), ("540,00", 800)],
        ]
    )
    sand, screws, cement = extract_price_lines(doc, CTX)
    assert sand.review_reasons == ("UNIT_AMBIGUOUS",)  # м² / м³ may have lost the superscript
    assert screws.review_reasons == ("UNIT_CORRECTED",)
    assert next(f for f in screws.fields if f.key == "unit").normalized == {"value": "pack"}
    assert cement.review_reasons == ()
    assert all(f.method == "ocr" for f in cement.fields)


def test_plain_m_from_a_text_layer_is_not_ambiguous() -> None:
    doc = page_lines(
        [
            [("Наименование", 100), ("Ед.", 600), ("Цена,", 800), ("руб.", 860)],
            [("Кабель", 100), ("м", 600), ("65,00", 800)],
        ],
        confidence=None,
    )
    (line,) = extract_price_lines(doc, CTX)
    assert line.review_reasons == ()
    assert all(f.method == "table_cell" for f in line.fields)


def test_header_repeated_with_ocr_differences_is_skipped() -> None:
    doc = page_lines(
        [
            [("Наименование", 100), ("Ед.", 600), ("Цена,", 800), ("руб.", 860)],
            [("Цемент", 100), ("шт.", 600), ("540,00", 800)],
            [("Наименование", 100), ("Ед.изм.", 600), ("Цена,руб.", 800)],
            [("Рубероид", 100), ("рулон", 600), ("620,00", 800)],
        ]
    )
    assert [fields(x)["name"] for x in extract_price_lines(doc, CTX)] == ["Цемент", "Рубероид"]


def test_headerless_table_roles_are_inferred_and_flagged() -> None:
    rows = [
        [
            ("КР-301", 50),
            ("Краска интерьерная белая", 200),
            ("шт.", 600),
            ("1950,00", 700),
            ("2290,00", 850),
        ],
        [
            ("КР-302", 50),
            ("Краска фасадная", 200),
            ("шт.", 600),
            ("2480,00", 700),
            ("2890,00", 850),
        ],
        [("КР-303", 50), ("Грунтовка глубокая", 200), ("л", 600), ("690,00", 700), ("790,00", 850)],
    ]
    doc = page_lines(rows)
    header = infer_header(doc.sheets[0])
    assert header is not None
    lines = extract_price_lines(doc, CTX)
    assert len(lines) == 3
    first = fields(lines[0])
    assert first["name"] == "Краска интерьерная белая"
    assert first["sku"] == "КР-301"
    assert (first["price_1"], first["price_2"]) == ("1950,00", "2290,00")
    assert "COLUMNS_INFERRED" in lines[0].review_reasons
    assert "MULTIPLE_PRICE_COLUMNS" in lines[0].review_reasons


def test_no_inference_without_prices() -> None:
    doc = page_lines([[("Просто", 100), ("текст", 400)]] * 4)
    assert infer_header(doc.sheets[0]) is None
    assert extract_price_lines(doc, CTX) == []
