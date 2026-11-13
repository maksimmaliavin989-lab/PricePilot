"""Legacy XLS adapter (xlrd 2.x, BIFF only). Same output as the XLSX adapter: cached values,
numbers converted with Excel's 15 significant digits, dates as ISO dates."""

from __future__ import annotations

from typing import Any

import xlrd

from pricepilot.dip.adapters.xlsx_adapter import _canonical, _number
from pricepilot.dip.limits import Limits
from pricepilot.dip.model import Cell, FileKind, ParseError, Row, Sheet, TableDocument


class XlsParser:
    name = "xls"
    version = "1.0.0"
    kinds = frozenset({FileKind.XLS})

    def parse(self, data: bytes, limits: Limits) -> TableDocument:
        try:
            book: Any = xlrd.open_workbook(file_contents=data, on_demand=True)
        except xlrd.XLRDError as exc:
            if "encrypt" in str(exc).lower():
                raise ParseError("PASSWORD_REQUIRED", "Файл защищён паролем.") from exc
            raise ParseError("INVALID_CONTENT", "Файл повреждён или не является XLS.") from exc
        except Exception as exc:  # xlrd raises assorted errors on broken BIFF data
            raise ParseError(
                "INVALID_CONTENT", "Файл повреждён: не удалось прочитать XLS."
            ) from exc
        try:
            if book.nsheets > limits.max_sheets:
                raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_sheets} листов.")
            sheets: list[Sheet] = []
            total = 0
            for s_index in range(book.nsheets):
                ws = book.sheet_by_index(s_index)
                if ws.ncols > limits.max_columns:
                    raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_columns} столбцов.")
                rows: list[Row] = []
                for r in range(ws.nrows):
                    total += 1
                    if total > limits.max_rows:
                        raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_rows} строк.")
                    cells: list[Cell] = []
                    for c in range(ws.ncols):
                        cell = ws.cell(r, c)
                        row, col = r + 1, c + 1
                        if cell.ctype in (
                            xlrd.XL_CELL_EMPTY,
                            xlrd.XL_CELL_BLANK,
                            xlrd.XL_CELL_ERROR,
                        ):
                            continue
                        if cell.ctype == xlrd.XL_CELL_NUMBER:
                            number = _number(cell.value)
                            cells.append(Cell(row, col, _canonical(number), number=number))
                        elif cell.ctype == xlrd.XL_CELL_DATE:
                            moment = xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
                            cells.append(Cell(row, col, moment.date().isoformat(), is_date=True))
                        elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                            cells.append(Cell(row, col, "TRUE" if cell.value else "FALSE"))
                        else:
                            text = str(cell.value).strip()
                            if len(text) > limits.max_cell_chars:
                                raise ParseError(
                                    "LIMIT_EXCEEDED",
                                    f"Ячейка длиннее {limits.max_cell_chars} символов.",
                                )
                            if text:
                                cells.append(Cell(row, col, text))
                    if cells:
                        rows.append(Row(index=r + 1, cells=tuple(cells)))
                sheets.append(Sheet(name=ws.name, rows=tuple(rows)))
                book.unload_sheet(s_index)
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(
                "INVALID_CONTENT", "Файл повреждён: не удалось прочитать XLS."
            ) from exc
        finally:
            book.release_resources()
        if not any(s.rows for s in sheets):
            raise ParseError("NO_TABLE_FOUND", "Книга не содержит заполненных ячеек.")
        return TableDocument(sheets=tuple(sheets), diagnostics={"sheets": str(len(sheets))})
