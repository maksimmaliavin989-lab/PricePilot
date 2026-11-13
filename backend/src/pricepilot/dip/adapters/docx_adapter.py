"""DOCX adapter (python-docx). Port of the prototype's ``extractDocx``: every table becomes a
sheet ("Таблица N"); a document without tables falls back to paragraphs split by tabs or
" | ". Macros and fields are never executed; only stored text is read.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from docx import Document
from docx.opc.exceptions import PackageNotFoundError

from pricepilot.dip.limits import Limits
from pricepilot.dip.model import Cell, FileKind, ParseError, Row, Sheet, TableDocument

_SPLIT = re.compile(r"\t+|\s\|\s")
_SPACES = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


class DocxParser:
    name = "docx"
    version = "1.0.0"
    kinds = frozenset({FileKind.DOCX})

    def parse(self, data: bytes, limits: Limits) -> TableDocument:
        try:
            document: Any = Document(io.BytesIO(data))
        except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise ParseError("INVALID_CONTENT", "Файл повреждён или не является DOCX.") from exc
        except Exception as exc:  # lxml/python-docx raise many types on broken XML
            raise ParseError(
                "INVALID_CONTENT", "Файл повреждён: не удалось прочитать DOCX."
            ) from exc

        sheets: list[Sheet] = []
        total = 0
        for t_index, table in enumerate(document.tables, start=1):
            if t_index > limits.max_sheets:
                raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_sheets} таблиц.")
            rows: list[Row] = []
            for r_index, row in enumerate(table.rows, start=1):
                total += 1
                if total > limits.max_rows:
                    raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_rows} строк.")
                cells: list[Cell] = []
                seen: set[int] = set()
                for c_index, cell in enumerate(row.cells, start=1):
                    if c_index > limits.max_columns:
                        raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_columns} столбцов.")
                    identity = id(cell._tc)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    text = _clean(cell.text)
                    if len(text) > limits.max_cell_chars:
                        raise ParseError(
                            "LIMIT_EXCEEDED", f"Ячейка длиннее {limits.max_cell_chars} символов."
                        )
                    if text:
                        cells.append(Cell(row=r_index, column=c_index, text=text))
                if cells:
                    rows.append(Row(index=r_index, cells=tuple(cells)))
            sheets.append(Sheet(name=f"Таблица {t_index}", rows=tuple(rows)))

        if not any(s.rows for s in sheets):
            rows = []
            for p_index, paragraph in enumerate(document.paragraphs, start=1):
                text = paragraph.text.strip()
                if not text:
                    continue
                parts = [_clean(p) for p in _SPLIT.split(text)]
                para_cells = tuple(
                    Cell(row=p_index, column=i, text=part)
                    for i, part in enumerate(parts, start=1)
                    if part
                )
                if para_cells:
                    rows.append(Row(index=p_index, cells=para_cells))
                if len(rows) > limits.max_rows:
                    raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_rows} строк.")
            sheets = [Sheet(name="Текст", rows=tuple(rows))]

        if not any(s.rows for s in sheets):
            raise ParseError("NO_TABLE_FOUND", "Документ не содержит текста.")
        return TableDocument(
            sheets=tuple(sheets), diagnostics={"tables": str(len(document.tables))}
        )
