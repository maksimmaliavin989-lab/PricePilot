"""CSV/TXT adapter: encoding and delimiter detection, strict limits, text-only cells.

Numbers stay text here: ``1 234,50`` vs ``1,234`` is a locale question for review, not for
the parser.
"""

from __future__ import annotations

import csv
import io

from pricepilot.dip.limits import Limits
from pricepilot.dip.model import Cell, FileKind, ParseError, Row, Sheet, TableDocument

_DELIMITERS = ";,\t|"


def _decode(data: bytes) -> tuple[str, str]:
    if b"\x00" in data:
        raise ParseError("INVALID_CONTENT", "Файл содержит двоичные данные и не похож на CSV.")
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="strict"), "cp1251"


def _delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in _DELIMITERS}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] else ","


class CsvParser:
    name = "csv"
    version = "1.0.0"
    kinds = frozenset({FileKind.CSV})

    def parse(self, data: bytes, limits: Limits) -> TableDocument:
        try:
            text, encoding = _decode(data)
        except UnicodeDecodeError as exc:
            raise ParseError("INVALID_CONTENT", "Не удалось определить кодировку файла.") from exc
        if not text.strip():
            raise ParseError("NO_TABLE_FOUND", "Файл не содержит строк.")
        delimiter = _delimiter(text[:65536])
        csv.field_size_limit(limits.max_cell_chars + 1)
        rows: list[Row] = []
        try:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
            for index, values in enumerate(reader, start=1):
                if index > limits.max_rows:
                    raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_rows} строк.")
                if len(values) > limits.max_columns:
                    raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_columns} столбцов.")
                cells = tuple(
                    Cell(row=index, column=col, text=value.strip())
                    for col, value in enumerate(values, start=1)
                    if value.strip()
                )
                if cells:
                    rows.append(Row(index=index, cells=cells))
        except csv.Error as exc:
            if "field larger than field limit" in str(exc):
                raise ParseError(
                    "LIMIT_EXCEEDED", f"Ячейка длиннее {limits.max_cell_chars} символов."
                ) from exc
            raise ParseError(
                "INVALID_CONTENT", "Файл повреждён: некорректная структура CSV."
            ) from exc
        if not rows:
            raise ParseError("NO_TABLE_FOUND", "Файл не содержит строк.")
        delimiter_name = {"\t": "tab"}.get(delimiter, delimiter)
        return TableDocument(
            sheets=(Sheet(name=None, rows=tuple(rows)),),
            diagnostics={"encoding": encoding, "delimiter": delimiter_name},
        )
