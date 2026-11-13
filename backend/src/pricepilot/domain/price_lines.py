"""PricePilot semantics on top of the DIP table model: which rows are products and which cells
are name, SKU, unit, characteristics and price.

Rules (IMPLEMENTATION_RULES.md, AGENTS.md):
* raw, normalized and provenance stay separate; a value that cannot be normalised safely is
  kept raw with ``normalized=None`` and the line gets a review reason;
* ambiguous numbers (``1,234`` / ``1.234``) are never guessed;
* several price columns are all kept and the line waits for an explicit choice;
* a currency that is not printed in the source is an assumption the user must confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pricepilot.dip.model import Cell, Row, Sheet, TableDocument
from pricepilot.domain.common import DomainValidationError
from pricepilot.domain.extraction import NewField, NewLine, SourceLocation, ValueType
from pricepilot.domain.values import MAX_FRACTION_DIGITS, MAX_INTEGER_DIGITS, decimal_text

HEADER_SCAN_ROWS = 30


class ReviewReason(StrEnum):
    MULTIPLE_PRICE_COLUMNS = "MULTIPLE_PRICE_COLUMNS"
    AMBIGUOUS_NUMBER = "AMBIGUOUS_NUMBER"
    INVALID_NUMBER = "INVALID_NUMBER"
    MISSING_PRICE = "MISSING_PRICE"
    MISSING_UNIT = "MISSING_UNIT"
    UNKNOWN_UNIT = "UNKNOWN_UNIT"
    ASSUMED_CURRENCY = "ASSUMED_CURRENCY"


class Role(StrEnum):
    NAME = "name"
    SKU = "sku"
    UNIT = "unit"
    CHARACTERISTICS = "characteristics"
    PRICE = "price"


# Order matters: the first matching role wins for a header cell.
_ROLE_PATTERNS: tuple[tuple[Role, re.Pattern[str]], ...] = (
    (Role.UNIT, re.compile(r"^ед\b|ед\.?\s*изм|единиц|^unit|\buom\b")),
    (Role.PRICE, re.compile(r"цен|стоимост|^опт\b|опт,|розниц|price|cost|руб|₽|\busd\b|\beur\b")),
    (
        Role.NAME,
        re.compile(r"наимен|товар|номенклатур|продукц|описан|product|description|^name|item"),
    ),
    (Role.SKU, re.compile(r"артикул|^код\b|^код$|\bsku\b|^арт\.?$|article|part\s*(no|number)")),
    (Role.CHARACTERISTICS, re.compile(r"характерист|параметр|размер|specification|^spec")),
)

_CURRENCY_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("RUB", re.compile(r"руб|₽|\brub\b|\bр\.")),
    ("USD", re.compile(r"\$|\busd\b|долл")),
    ("EUR", re.compile(r"€|\beur\b|евро")),
    ("CNY", re.compile(r"¥|\bcny\b|юан")),
    ("KZT", re.compile(r"₸|\bkzt\b|тенге")),
)

_UNIT_ALIASES: dict[str, str] = {
    **dict.fromkeys(["шт", "штук", "штука", "штуки", "pc", "pcs", "piece", "ea"], "pc"),
    **dict.fromkeys(["уп", "упак", "упаковка", "pack", "pkg"], "pack"),
    **dict.fromkeys(["компл", "комплект", "кт", "к-т", "set"], "set"),
    **dict.fromkeys(["лист", "листов", "sheet"], "sheet"),
    **dict.fromkeys(["меш", "мешок", "мешка", "bag"], "bag"),
    **dict.fromkeys(["рул", "рулон", "roll"], "roll"),
    **dict.fromkeys(["м", "пм", "п.м", "погм", "пог.м", "мп", "m", "метр"], "m"),
    **dict.fromkeys(["м2", "м²", "кв.м", "квм", "m2", "m²", "sqm"], "m2"),
    **dict.fromkeys(["м3", "м³", "куб.м", "кубм", "m3", "m³", "cbm"], "m3"),
    **dict.fromkeys(["л", "литр", "l"], "l"),
    **dict.fromkeys(["кг", "kg"], "kg"),
    **dict.fromkeys(["т", "тн", "тонна", "t"], "t"),
}

_CURRENCY_TOKENS = re.compile(r"(руб\.?|р\.|₽|rub|usd|eur|\$|€)", re.IGNORECASE)
_SPACES = re.compile(r"[\s  ]+")


def _norm_header(text: str) -> str:
    return _SPACES.sub(" ", text.strip().lower().replace("ё", "е"))


def role_of(header: str) -> Role | None:
    value = _norm_header(header)
    for role, pattern in _ROLE_PATTERNS:
        if pattern.search(value):
            return role
    return None


def currency_from_header(header: str) -> str | None:
    value = _norm_header(header)
    for code, pattern in _CURRENCY_MARKERS:
        if pattern.search(value):
            return code
    return None


def unit_from_text(text: str) -> str | None:
    key = _SPACES.sub("", text.strip().lower().replace("ё", "е")).rstrip(".")
    return _UNIT_ALIASES.get(key) or _UNIT_ALIASES.get(key.replace(".", ""))


@dataclass(frozen=True, slots=True)
class NumberReading:
    value: Decimal | None
    problem: ReviewReason | None


_PLAIN = re.compile(r"\d+")
_DECIMAL = re.compile(r"(\d+)[.,](\d{1,2}|\d{4,})")  # 1.5, 12,50, 0.0005 — not 3 digits
_AMBIGUOUS = re.compile(r"\d{1,3}[.,]\d{3}")  # 1,234 / 1.234: thousands or decimals?
_SPACE_GROUPS = re.compile(r"\d{1,3}(?: \d{3})+(?:[.,]\d+)?")
_COMMA_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+\.\d+|\d{1,3}(?:,\d{3}){2,}")
_DOT_THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+,\d+|\d{1,3}(?:\.\d{3}){2,}")


def _bounded(text: str) -> NumberReading:
    try:
        number = Decimal(text)
    except InvalidOperation:
        return NumberReading(None, ReviewReason.INVALID_NUMBER)
    sign, digits, exponent = number.as_tuple()
    assert isinstance(exponent, int)  # noqa: S101 - finite by construction
    fraction = max(0, -exponent)
    integer = max(1, len(digits) + exponent)
    if sign or fraction > MAX_FRACTION_DIGITS or integer > MAX_INTEGER_DIGITS:
        return NumberReading(None, ReviewReason.INVALID_NUMBER)
    return NumberReading(number, None)


def read_number(text: str) -> NumberReading:
    """Interpret a price written as text without assuming a locale."""
    value = _SPACES.sub(" ", _CURRENCY_TOKENS.sub("", text)).strip()
    if not value:
        return NumberReading(None, ReviewReason.MISSING_PRICE)
    if _PLAIN.fullmatch(value):
        return _bounded(value)
    if _SPACE_GROUPS.fullmatch(value):
        return _bounded(value.replace(" ", "").replace(",", "."))
    if _AMBIGUOUS.fullmatch(value):
        return NumberReading(None, ReviewReason.AMBIGUOUS_NUMBER)
    if _DECIMAL.fullmatch(value):
        return _bounded(value.replace(",", "."))
    if _COMMA_THOUSANDS.fullmatch(value):
        return _bounded(value.replace(",", ""))
    if _DOT_THOUSANDS.fullmatch(value):
        return _bounded(value.replace(".", "").replace(",", "."))
    return NumberReading(None, ReviewReason.INVALID_NUMBER)


@dataclass(frozen=True, slots=True)
class Column:
    index: int
    role: Role
    header: str


@dataclass(frozen=True, slots=True)
class HeaderMatch:
    row: Row
    columns: tuple[Column, ...]

    def of(self, role: Role) -> list[Column]:
        return [c for c in self.columns if c.role is role]


def find_header(sheet: Sheet) -> HeaderMatch | None:
    """First row (within the top rows) that names a product column and a price column."""
    best: HeaderMatch | None = None
    for row in sheet.rows[:HEADER_SCAN_ROWS]:
        columns = tuple(
            Column(cell.column, role, cell.text)
            for cell in row.cells
            if not cell.number and (role := role_of(cell.text)) is not None
        )
        roles = {c.role for c in columns}
        if Role.NAME in roles and Role.PRICE in roles:
            if best is None or len(roles) > len({c.role for c in best.columns}):
                best = HeaderMatch(row=row, columns=columns)
            if len(roles) >= 3:
                break
    return best


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    fallback_currency: str  # supplier default, else workspace base currency


def _text_field(key: str, cell: Cell, sheet: str | None, header: str) -> NewField:
    return NewField(
        key=key,
        value_type=ValueType.TEXT,
        raw_value=cell.text,
        normalized={"value": cell.text},
        confidence=None,
        location=_cell_location(sheet, cell),
        method="table_cell",
        header=header,
        formula=cell.formula,
    )


def _cell_location(sheet: str | None, cell: Cell) -> SourceLocation:
    return SourceLocation(sheet=sheet, row=cell.row, column=cell.column, cell=cell.ref)


def _price_field(
    key: str,
    column: Column,
    cell: Cell | None,
    currency: str,
    sheet: str | None,
    row: Row,
) -> tuple[NewField | None, ReviewReason | None]:
    if cell is None:
        return None, ReviewReason.MISSING_PRICE
    if cell.number is not None:
        reading = _bounded(decimal_text(cell.number))
    else:
        reading = read_number(cell.text)
    normalized = None
    if reading.value is not None:
        try:
            normalized = {"amount": decimal_text(reading.value), "currency": currency}
        except DomainValidationError:  # pragma: no cover - bounded above
            reading = NumberReading(None, ReviewReason.INVALID_NUMBER)
    return (
        NewField(
            key=key,
            value_type=ValueType.MONEY,
            raw_value=cell.text,
            normalized=normalized,
            confidence=None,
            location=_cell_location(sheet, cell),
            method="table_cell",
            header=column.header,
            formula=cell.formula,
        ),
        reading.problem,
    )


def _is_section(row: Row, name_col: int, header: HeaderMatch) -> bool:
    """A heading such as "Цемент, песок, щебень": one text cell, no price/unit."""
    data_columns = {c.index for c in header.columns if c.role in {Role.PRICE, Role.UNIT}}
    has_data = any(cell.column in data_columns for cell in row.cells)
    return len(row.cells) == 1 and not has_data and row.cells[0].number is None


def _sheet_lines(sheet: Sheet, ctx: ExtractionContext) -> list[NewLine]:
    header = find_header(sheet)
    if header is None:
        return []
    name_col = header.of(Role.NAME)[0]
    prices = header.of(Role.PRICE)
    lines: list[NewLine] = []
    section: str | None = None
    for row in sheet.rows:
        if row.index <= header.row.index:
            continue
        name = row.cell(name_col.index)
        if name is None or name.number is not None:
            if _is_section(row, name_col.index, header):
                section = row.cells[0].text
            continue
        price_cells = [row.cell(c.index) for c in prices]
        unit_columns = header.of(Role.UNIT)
        unit_cell = row.cell(unit_columns[0].index) if unit_columns else None
        if all(c is None for c in price_cells) and unit_cell is None:
            if _is_section(row, name_col.index, header):
                section = name.text
            continue  # text without price/unit: heading, note or footer, not a product

        reasons: list[ReviewReason] = []
        fields: list[NewField] = [_text_field("name", name, sheet.name, name_col.header)]
        for role, key in ((Role.SKU, "sku"), (Role.CHARACTERISTICS, "characteristics")):
            for column in header.of(role)[:1]:
                if (cell := row.cell(column.index)) is not None:
                    fields.append(_text_field(key, cell, sheet.name, column.header))
        if section:
            fields.append(
                NewField(
                    key="section",
                    value_type=ValueType.TEXT,
                    raw_value=section,
                    normalized={"value": section},
                    confidence=None,
                    location=SourceLocation(sheet=sheet.name),
                    method="table_section",
                )
            )

        if unit_cell is None:
            reasons.append(ReviewReason.MISSING_UNIT)
        else:
            code = unit_from_text(unit_cell.text)
            if code is None:
                reasons.append(ReviewReason.UNKNOWN_UNIT)
            fields.append(
                NewField(
                    key="unit",
                    value_type=ValueType.UNIT,
                    raw_value=unit_cell.text,
                    normalized={"value": code} if code else None,
                    confidence=None,
                    location=_cell_location(sheet.name, unit_cell),
                    method="table_cell",
                    header=unit_columns[0].header,
                )
            )

        if len(prices) > 1:
            reasons.append(ReviewReason.MULTIPLE_PRICE_COLUMNS)
        for n, (column, cell) in enumerate(zip(prices, price_cells, strict=True), start=1):
            currency = currency_from_header(column.header)
            if currency is None:
                currency = ctx.fallback_currency
                reasons.append(ReviewReason.ASSUMED_CURRENCY)
            key = "price" if len(prices) == 1 else f"price_{n}"
            field, problem = _price_field(key, column, cell, currency, sheet.name, row)
            if field is not None:
                fields.append(field)
            if problem is not None:
                reasons.append(problem)

        lines.append(
            NewLine(
                raw_text=" | ".join(cell.text for cell in row.cells)[:10_000],
                location=SourceLocation(sheet=sheet.name, row=row.index),
                fields=tuple(fields),
                review_reasons=tuple(r.value for r in dict.fromkeys(reasons)),
            )
        )
    return lines


def extract_price_lines(document: TableDocument, ctx: ExtractionContext) -> list[NewLine]:
    """All product lines of all sheets, in source order. Empty result → ``NO_TABLE_FOUND``."""
    lines: list[NewLine] = []
    for sheet in document.sheets:
        lines.extend(_sheet_lines(sheet, ctx))
    return lines
