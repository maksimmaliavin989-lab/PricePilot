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
from pricepilot.domain.extraction import NewField, NewLine, Region, SourceLocation, ValueType
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
    COLUMNS_INFERRED = "COLUMNS_INFERRED"  # no readable header: roles guessed from content
    UNIT_CORRECTED = "UNIT_CORRECTED"  # unit read after fixing OCR look-alike characters
    UNIT_AMBIGUOUS = "UNIT_AMBIGUOUS"  # recognised "м": a lost superscript would mean м²/м³


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


class PriceType(StrEnum):
    """Commercial meaning of a price column, read from its header. Several typed prices in
    one list (e.g. опт / розница) are a property of the list, not an ambiguity: all are
    kept as separate fields and the comparison decides which applies (TASK_006)."""

    WHOLESALE = "wholesale"
    RETAIL = "retail"
    PROMO = "promo"


_PRICE_TYPES: tuple[tuple[PriceType, re.Pattern[str]], ...] = (
    (PriceType.PROMO, re.compile(r"акци|скидк|распрод|promo|\bsale\b")),  # not "wholesale"
    (PriceType.WHOLESALE, re.compile(r"(?:^|[^а-я])опт|wholesale|дилер")),
    (PriceType.RETAIL, re.compile(r"розн|retail")),
)


def price_type_of(header: str) -> PriceType | None:
    value = _norm_header(header)
    for price_type, pattern in _PRICE_TYPES:
        if pattern.search(value):
            return price_type
    return None


def price_keys(headers: list[str]) -> tuple[list[str], bool]:
    """Field keys for the price columns and whether the user must pick one.

    One column → ``price``. Several columns with distinct recognised types →
    ``price_wholesale`` / ``price_retail`` / ``price_promo``, no review needed. Otherwise
    (unlabelled or repeated types) → ``price_1…price_n`` and an explicit choice is required.
    """
    if len(headers) == 1:
        return ["price"], False
    types = [price_type_of(h) for h in headers]
    if all(t is not None for t in types) and len(set(types)) == len(types):
        return [f"price_{t.value}" for t in types if t is not None], False
    return [f"price_{n}" for n in range(1, len(headers) + 1)], True


def currency_from_header(header: str) -> str | None:
    value = _norm_header(header)
    for code, pattern in _CURRENCY_MARKERS:
        if pattern.search(value):
            return code
    return None


# Latin letters that look like Cyrillic ones; OCR mixes them inside one word ("шT.").
_LOOKALIKES = str.maketrans("acekmoptxyh", "асекмортхун")
_CYRILLIC = re.compile(r"[а-я]")
_LATIN = re.compile(r"[a-z]")


# Recognition confusions seen on scans: Latin look-alikes, "з" for "3", markup for "²/³".
_OCR_LOOKALIKES = str.maketrans("acekmoptxyhnru", "асекмортхунпги")
_OCR_NOISE = re.compile(r"[\^\\'`]")


def unit_from_ocr_text(text: str) -> tuple[str | None, bool]:
    """Unit code and whether it was obtained only after correcting likely recognition errors
    ("yn." → "уп.", "Kr" → "кг", "мз" → "м3"). A corrected unit must be confirmed."""
    exact = unit_from_text(text)
    if exact is not None:
        return exact, False
    key = _OCR_NOISE.sub("", text.strip().lower()).translate(_OCR_LOOKALIKES)
    key = re.sub(r"(?<=м)з$", "3", key)
    corrected = unit_from_text(key)
    return corrected, corrected is not None


def unit_from_text(text: str) -> str | None:
    key = _SPACES.sub("", text.strip().lower().replace("ё", "е")).rstrip(".")
    found = _UNIT_ALIASES.get(key) or _UNIT_ALIASES.get(key.replace(".", ""))
    if found is None and _CYRILLIC.search(key) and _LATIN.search(key):
        mapped = key.translate(_LOOKALIKES)
        found = _UNIT_ALIASES.get(mapped) or _UNIT_ALIASES.get(mapped.replace(".", ""))
    return found


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


_PRICE_LIKE = re.compile(r"\d[.,]\d{2}$")
_CODE_LIKE = re.compile(r"^(?=.*\d)(?=.*[A-Za-zА-Яа-яЁё])[\w./-]{2,20}$")


def infer_header(sheet: Sheet) -> HeaderMatch | None:
    """Column roles from content when no header row is readable (e.g. OCR could not read a
    header printed light-on-dark). Port of the prototype's headerless recovery:
    prices = columns of numbers with kopecks, unit = column of unit tokens, name = the
    longest text column, SKU = short letter+digit codes left of the name. Row-number
    columns (plain integers) are ignored. Every line produced this way is flagged."""
    rows = [r for r in sheet.rows if len(r.cells) >= 2]
    if len(rows) < 3:
        return None
    columns = sorted({c.column for r in rows for c in r.cells})
    stats: dict[int, dict[str, float]] = {}
    for col in columns:
        texts = [c.text for r in rows if (c := r.cell(col)) is not None]
        n = len(texts)
        if n < max(2, len(rows) // 3):
            continue
        stats[col] = {
            "price": sum(
                1
                for t in texts
                if _PRICE_LIKE.search(t.strip()) and read_number(t).value is not None
            )
            / n,
            "unit": sum(1 for t in texts if unit_from_text(t)) / n,
            "code": sum(1 for t in texts if _CODE_LIKE.match(t.strip())) / n,
            "letters": sum(1 for t in texts if re.search(r"[A-Za-zА-Яа-яЁё]{3}", t)) / n,
            "length": sum(len(t) for t in texts) / n,
        }
    prices = [c for c, st in stats.items() if st["price"] >= 0.6]
    units = [c for c, st in stats.items() if st["unit"] >= 0.5 and c not in prices]
    names = [c for c, st in stats.items() if st["letters"] >= 0.6 and c not in prices + units]
    if not prices or not names:
        return None
    name_col = max(names, key=lambda c: stats[c]["length"])
    roles: list[Column] = [Column(name_col, Role.NAME, "")]
    if units:
        roles.append(Column(max(units, key=lambda c: stats[c]["unit"]), Role.UNIT, ""))
    codes = [c for c, st in stats.items() if c < name_col and st["code"] >= 0.6 and c not in prices]
    if codes:
        roles.append(Column(codes[-1], Role.SKU, ""))
    roles.extend(Column(c, Role.PRICE, "") for c in sorted(prices) if c > name_col)
    if not any(r.role is Role.PRICE for r in roles):
        return None
    return HeaderMatch(
        row=Row(index=0, cells=()), columns=tuple(sorted(roles, key=lambda c: c.index))
    )


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    fallback_currency: str  # supplier default, else workspace base currency


def _text_field(key: str, cell: Cell, sheet: str | None, header: str) -> NewField:
    return NewField(
        key=key,
        value_type=ValueType.TEXT,
        raw_value=cell.text,
        normalized={"value": cell.text},
        confidence=cell.confidence,
        location=_cell_location(sheet, cell),
        method=_method(cell),
        header=header,
        formula=cell.formula,
    )


def _method(cell: Cell) -> str:
    """Recognised (OCR) text is marked so its origin stays visible in provenance."""
    return "ocr" if cell.confidence is not None else "table_cell"


def _cell_location(sheet: str | None, cell: Cell) -> SourceLocation:
    if not cell.has_cell_address:  # PDF/OCR: page + bounding box instead of an A1 address
        region = Region(*cell.region) if cell.region else None
        return SourceLocation(page=cell.page, row=cell.row, column=cell.column, region=region)
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
            confidence=cell.confidence,
            location=_cell_location(sheet, cell),
            method=_method(cell),
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


def _name_with_continuation(row: Row, name: Cell, header: HeaderMatch) -> Cell:
    """On a page (PDF/OCR) a long name may be laid out as two segments, the second landing
    in a column without a header ("Цемент М500 ДО," | "мешок 50 кг"). Such cells between
    the name column and the next recognised column belong to the name. Spreadsheet cells
    are never merged: their columns are explicit."""
    if name.has_cell_address or not header.row.cells:  # inferred roles: columns unknown
        return name
    following = [c.index for c in header.columns if c.index > name.column]
    stop = min(following, default=name.column + 1)
    extra = [
        c
        for c in row.cells
        if name.column < c.column < stop and header.row.cell(c.column) is None and c.region
    ]
    if not extra or name.region is None:
        return name
    parts = [name, *extra]
    regions = [p.region for p in parts if p.region is not None]
    x0 = min(r[0] for r in regions)
    y0 = min(r[1] for r in regions)
    x1 = max(r[0] + r[2] for r in regions)
    y1 = max(r[1] + r[3] for r in regions)
    return Cell(
        row=name.row,
        column=name.column,
        text=" ".join(p.text for p in parts),
        page=name.page,
        region=(x0, y0, round(x1 - x0, 4), round(y1 - y0, 4)),
        confidence=min((p.confidence for p in parts if p.confidence is not None), default=None),
    )


def _repeats_header(row: Row, header: HeaderMatch) -> bool:
    """A header repeated on the next page, possibly recognised slightly differently by OCR
    ("Цена,руб." / "Цена, руб."): the name and every price column hold header words."""
    if not header.row.cells:
        return False
    for column in [*header.of(Role.NAME), *header.of(Role.PRICE)]:
        cell = row.cell(column.index)
        if cell is None or role_of(cell.text) is not column.role:
            return False
    return True


def _sheet_lines(sheet: Sheet, ctx: ExtractionContext) -> list[NewLine]:
    header = find_header(sheet)
    inferred = False
    if header is None:
        header = infer_header(sheet)
        inferred = True
    if header is None:
        return []
    name_col = header.of(Role.NAME)[0]
    prices = header.of(Role.PRICE)
    lines: list[NewLine] = []
    section: str | None = None
    header_texts = [_norm_header(c.text) for c in header.row.cells]
    for row in sheet.rows:
        if row.index <= header.row.index:
            continue
        if [_norm_header(c.text) for c in row.cells] == header_texts or _repeats_header(
            row, header
        ):
            continue  # the header repeated on a new page (multi-page PDF/DOCX tables)
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

        name = _name_with_continuation(row, name, header)
        reasons: list[ReviewReason] = [ReviewReason.COLUMNS_INFERRED] if inferred else []
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
            code, corrected = unit_from_ocr_text(unit_cell.text)
            if code is None:
                reasons.append(ReviewReason.UNKNOWN_UNIT)
            elif corrected:
                reasons.append(ReviewReason.UNIT_CORRECTED)
            elif code == "m" and unit_cell.confidence is not None:
                reasons.append(ReviewReason.UNIT_AMBIGUOUS)
            fields.append(
                NewField(
                    key="unit",
                    value_type=ValueType.UNIT,
                    raw_value=unit_cell.text,
                    normalized={"value": code} if code else None,
                    confidence=unit_cell.confidence,
                    location=_cell_location(sheet.name, unit_cell),
                    method=_method(unit_cell),
                    header=unit_columns[0].header,
                )
            )

        keys, must_choose = price_keys([c.header for c in prices])
        if must_choose:
            reasons.append(ReviewReason.MULTIPLE_PRICE_COLUMNS)
        for n, (column, cell) in enumerate(zip(prices, price_cells, strict=True), start=1):
            currency = currency_from_header(column.header)
            if currency is None:
                currency = ctx.fallback_currency
                reasons.append(ReviewReason.ASSUMED_CURRENCY)
            key = keys[n - 1]
            field, problem = _price_field(key, column, cell, currency, sheet.name, row)
            if field is not None:
                fields.append(field)
            if problem is not None:
                reasons.append(problem)

        lines.append(
            NewLine(
                raw_text=" | ".join(cell.text for cell in row.cells)[:10_000],
                location=SourceLocation(sheet=sheet.name, row=row.index, page=row.page),
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
