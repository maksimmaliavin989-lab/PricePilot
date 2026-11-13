"""Digital PDF adapter (pdfplumber/pdfminer.six, text layer only).

Port of the prototype's ``groupPdfTextItems`` + ``pdfLinesToRows``:
1. words are grouped into lines by their vertical position;
2. neighbouring words of a line are joined into segments (a gap wider than ~1.2 font
   heights starts a new segment, i.e. a new column);
3. the column layout comes from the first line with the most segments (the table header)
   and every segment is assigned to the nearest column;
4. pages continue one table: the result is a single sheet, each cell keeps its page and its
   bounding box normalised to the page (0..1), so the UI can point at the exact place.

Pages without a text layer (scans) need OCR. Until an OCR engine is connected the document
fails with ``OCR_RESOURCE_MISSING`` instead of silently skipping pages.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pdfplumber.utils.exceptions import PdfminerException

from pricepilot.dip.limits import Limits
from pricepilot.dip.model import Cell, FileKind, ParseError, Row, Sheet, TableDocument

HEADER_SCAN_LINES = 40


@dataclass(frozen=True, slots=True)
class _Segment:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    words: tuple[_Segment, ...] = ()

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True, slots=True)
class _Line:
    page: int
    width: float
    height: float
    segments: tuple[_Segment, ...]


def _lines(words: list[dict[str, Any]], page: int, width: float, height: float) -> list[_Line]:
    words = sorted(words, key=lambda w: (round(float(w["top"]), 1), float(w["x0"])))
    grouped: list[list[dict[str, Any]]] = []
    for word in words:
        size = float(word["bottom"]) - float(word["top"])
        for line in grouped:
            if abs(float(line[0]["top"]) - float(word["top"])) <= max(2.0, size * 0.42):
                line.append(word)
                break
        else:
            grouped.append([word])
    result: list[_Line] = []
    for line in sorted(grouped, key=lambda ws: min(float(w["top"]) for w in ws)):
        line.sort(key=lambda w: float(w["x0"]))
        segments: list[_Segment] = []
        for word in line:
            size = float(word["bottom"]) - float(word["top"])
            x0, x1 = float(word["x0"]), float(word["x1"])
            top, bottom = float(word["top"]), float(word["bottom"])
            last = segments[-1] if segments else None
            piece = _Segment(str(word["text"]), x0, x1, top, bottom)
            if last and x0 - last.x1 <= max(3.0, size * 1.2):
                segments[-1] = _Segment(
                    f"{last.text} {piece.text}",
                    last.x0,
                    x1,
                    min(last.top, top),
                    max(last.bottom, bottom),
                    (*(last.words or (last,)), piece),
                )
            else:
                segments.append(piece)
        result.append(_Line(page, width, height, tuple(segments)))
    return result


def _assign(
    segment: _Segment, columns: list[_Segment], tabular: bool
) -> list[tuple[int, _Segment]]:
    """In a table row (``tabular``: the line has several segments) a segment that spans
    several columns — e.g. tightly set headers "Опт, руб. Розница, руб." — is split into its
    words, each assigned on its own. A single-segment line (title, footer note) stays whole."""
    spanned = [c for c in columns if min(segment.x1, c.x1) - max(segment.x0, c.x0) > 0]
    if tabular and len(spanned) >= 2 and len(segment.words) > 1:
        return [(_column_of(word, columns), word) for word in segment.words]
    return [(_column_of(segment, columns), segment)]


def _column_of(segment: _Segment, columns: list[_Segment]) -> int:
    """Column whose header overlaps the segment most; nearest centre otherwise."""
    best, best_overlap = -1, 0.0
    for index, column in enumerate(columns):
        overlap = min(segment.x1, column.x1) - max(segment.x0, column.x0)
        if overlap > best_overlap:
            best, best_overlap = index, overlap
    if best >= 0:
        return best
    return min(range(len(columns)), key=lambda i: abs(columns[i].center - segment.center))


def _region(s: _Segment, line: _Line) -> tuple[float, float, float, float]:
    def clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    x, y = clamp(s.x0 / line.width), clamp(s.top / line.height)
    w = clamp(s.x1 / line.width) - x
    h = clamp(s.bottom / line.height) - y
    return (round(x, 4), round(y, 4), round(max(w, 0.0), 4), round(max(h, 0.0), 4))


class PdfParser:
    name = "pdf-text"
    version = "1.0.0"
    kinds = frozenset({FileKind.PDF})

    def parse(self, data: bytes, limits: Limits) -> TableDocument:
        lines: list[_Line] = []
        missing_text: list[int] = []
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                if len(pdf.pages) > limits.max_pages:
                    raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_pages} страниц.")
                for number, page in enumerate(pdf.pages, start=1):
                    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
                    if not words:
                        missing_text.append(number)
                        continue
                    lines.extend(_lines(words, number, float(page.width), float(page.height)))
                    if len(lines) > limits.max_rows:
                        raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_rows} строк.")
                page_count = len(pdf.pages)
        except ParseError:
            raise
        except (PDFPasswordIncorrect, PDFEncryptionError) as exc:
            raise ParseError("PASSWORD_REQUIRED", "PDF защищён паролем.") from exc
        except PdfminerException as exc:
            if isinstance(
                exc.args[0] if exc.args else None, PDFPasswordIncorrect | PDFEncryptionError
            ):
                raise ParseError("PASSWORD_REQUIRED", "PDF защищён паролем.") from exc
            raise ParseError("INVALID_CONTENT", "Файл повреждён или не является PDF.") from exc
        except (PDFSyntaxError, ValueError, KeyError, OSError) as exc:
            raise ParseError("INVALID_CONTENT", "Файл повреждён или не является PDF.") from exc
        except Exception as exc:  # pdfminer raises a wide range of errors on broken files
            raise ParseError(
                "INVALID_CONTENT", "Файл повреждён: не удалось прочитать PDF."
            ) from exc

        if missing_text:
            pages = ", ".join(str(p) for p in missing_text)
            raise ParseError(
                "OCR_RESOURCE_MISSING",
                f"Страницы без текстового слоя (скан): {pages}. Для них нужно распознавание "
                "текста, оно ещё не подключено. Оригинал сохранён.",
            )
        if not lines:
            raise ParseError("NO_TABLE_FOUND", "В PDF нет текста.")

        # Column layout: the first line with the most separate segments (usually the first
        # data row, whose values are spaced further apart than a tightly set header).
        header = max(lines[:HEADER_SCAN_LINES], key=lambda ln: len(ln.segments))
        columns = list(header.segments)
        rows: list[Row] = []
        for index, line in enumerate(lines, start=1):
            texts: dict[int, list[_Segment]] = {}
            if len(columns) >= 2:
                tabular = len(line.segments) >= 2
                for segment in line.segments:
                    for column, piece in _assign(segment, columns, tabular):
                        texts.setdefault(column, []).append(piece)
            else:
                texts[0] = list(line.segments)
            cells = []
            for column, segments in sorted(texts.items()):
                merged = _Segment(
                    " ".join(s.text for s in segments),
                    min(s.x0 for s in segments),
                    max(s.x1 for s in segments),
                    min(s.top for s in segments),
                    max(s.bottom for s in segments),
                )
                if len(merged.text) > limits.max_cell_chars:
                    raise ParseError(
                        "LIMIT_EXCEEDED", f"Ячейка длиннее {limits.max_cell_chars} символов."
                    )
                cells.append(
                    Cell(
                        row=index,
                        column=column + 1,
                        text=merged.text,
                        page=line.page,
                        region=_region(merged, line),
                    )
                )
            rows.append(Row(index=index, cells=tuple(cells), page=line.page))
        return TableDocument(
            sheets=(Sheet(name=None, rows=tuple(rows)),),
            diagnostics={"pages": str(page_count), "columns": str(len(columns))},
        )
