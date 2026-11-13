"""XLSX adapter (openpyxl, read-only, defusedxml-protected).

* Formulas are never evaluated: the value is the one cached by the authoring application and
  the formula text is kept alongside it (DOCUMENT_ENGINE.md: "fix both when available").
* Numeric cells are binary doubles in the file; they are converted to Decimal with Excel's
  15 significant digits so ``0.1 + 0.2`` does not appear as ``0.30000000000000004``.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from pricepilot.dip.limits import Limits
from pricepilot.dip.model import Cell, FileKind, ParseError, Row, Sheet, TableDocument


def _number(value: int | float) -> Decimal:
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(format(value, ".15g"))
    except InvalidOperation as exc:  # pragma: no cover - finite floats always convert
        raise ParseError("INVALID_CONTENT", "Некорректное число в ячейке.") from exc


def _canonical(number: Decimal) -> str:
    return format(number, "f")


def _cell(row: int, column: int, value: Any, formula: str | None, limits: Limits) -> Cell | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return Cell(row=row, column=column, text="TRUE" if value else "FALSE", formula=formula)
    if isinstance(value, int | float):
        if isinstance(value, float) and value != value:  # NaN
            return None
        number = _number(value)
        return Cell(row=row, column=column, text=_canonical(number), number=number, formula=formula)
    if isinstance(value, datetime | date | time):
        text = value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
        return Cell(row=row, column=column, text=text, formula=formula, is_date=True)
    text = str(value).strip()
    if len(text) > limits.max_cell_chars:
        raise ParseError("LIMIT_EXCEEDED", f"Ячейка длиннее {limits.max_cell_chars} символов.")
    return Cell(row=row, column=column, text=text, formula=formula)


class XlsxParser:
    name = "xlsx"
    version = "1.0.0"
    kinds = frozenset({FileKind.XLSX})

    def _open(self, data: bytes, *, values: bool) -> Any:
        try:
            return load_workbook(
                io.BytesIO(data), read_only=True, data_only=values, keep_links=False
            )
        except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
            raise ParseError("INVALID_CONTENT", "Файл повреждён или не является XLSX.") from exc
        except Exception as exc:  # openpyxl raises a wide range of errors on broken XML
            raise ParseError(
                "INVALID_CONTENT", "Файл повреждён: не удалось прочитать XLSX."
            ) from exc

    def _formulas(self, data: bytes, limits: Limits) -> dict[tuple[str, int, int], str]:
        workbook = self._open(data, values=False)
        found: dict[tuple[str, int, int], str] = {}
        try:
            for ws in workbook.worksheets[: limits.max_sheets]:
                for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    if r > limits.max_rows:
                        break
                    for c, value in enumerate(row[: limits.max_columns], start=1):
                        if isinstance(value, str) and value.startswith("="):
                            found[(ws.title, r, c)] = value[:1000]
        finally:
            workbook.close()
        return found

    def parse(self, data: bytes, limits: Limits) -> TableDocument:
        formulas = self._formulas(data, limits)
        workbook = self._open(data, values=True)
        sheets: list[Sheet] = []
        total_rows = 0
        try:
            worksheets = workbook.worksheets
            if len(worksheets) > limits.max_sheets:
                raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_sheets} листов.")
            for ws in worksheets:
                rows: list[Row] = []
                for r, values in enumerate(ws.iter_rows(values_only=True), start=1):
                    total_rows += 1
                    if total_rows > limits.max_rows:
                        raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_rows} строк.")
                    if len(values) > limits.max_columns and any(
                        v is not None for v in values[limits.max_columns :]
                    ):
                        raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_columns} столбцов.")
                    cells = tuple(
                        cell
                        for c, v in enumerate(values[: limits.max_columns], start=1)
                        if (cell := _cell(r, c, v, formulas.get((ws.title, r, c)), limits))
                    )
                    if cells:
                        rows.append(Row(index=r, cells=cells))
                sheets.append(Sheet(name=ws.title, rows=tuple(rows)))
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(
                "INVALID_CONTENT", "Файл повреждён: не удалось прочитать XLSX."
            ) from exc
        finally:
            workbook.close()
        if not any(sheet.rows for sheet in sheets):
            raise ParseError("NO_TABLE_FOUND", "Книга не содержит заполненных ячеек.")
        return TableDocument(
            sheets=tuple(sheets),
            diagnostics={"sheets": str(len(sheets)), "formulas": str(len(formulas))},
        )
