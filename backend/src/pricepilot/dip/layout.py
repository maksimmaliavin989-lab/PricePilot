"""Page layout → table rows, shared by the digital-PDF adapter and OCR adapters.

Port of the prototype's ``groupPdfTextItems`` + ``pdfLinesToRows`` / ``ocrPageToMatrix``:
1. boxes (words or OCR fragments) are grouped into lines by vertical position;
2. neighbouring boxes of a line join into segments (a gap wider than ~1.2 text heights
   starts a new segment, i.e. a new column);
3. the column layout comes from the first line with the most segments and every segment is
   assigned to the column it overlaps most (nearest centre otherwise); in table rows a
   segment spanning several columns is split into its words;
4. pages continue one table; each cell keeps its page and its box normalised to the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pricepilot.dip.limits import Limits
from pricepilot.dip.model import Cell, ParseError, Row

HEADER_SCAN_LINES = 40


@dataclass(frozen=True, slots=True)
class Box:
    """A word or text fragment with its box in page units (top-left origin)."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    words: tuple[Box, ...] = ()
    confidence: float | None = None  # OCR only

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True, slots=True)
class PageLine:
    page: int
    width: float
    height: float
    segments: tuple[Box, ...]


def _char_width(box: Box) -> float:
    return (box.x1 - box.x0) / max(1, len(box.text))


def _vertical_overlap(box: Box, top: float, bottom: float) -> float:
    """Overlap with a line span, relative to the smaller of the two heights."""
    overlap = min(box.bottom, bottom) - max(box.top, top)
    return overlap / max(1e-6, min(box.bottom - box.top, bottom - top))


def group_lines(boxes: list[Box], page: int, width: float, height: float) -> list[PageLine]:
    """Group word/fragment boxes into text lines, then into column segments.

    A box joins a line when they overlap vertically by at least half of the smaller height
    (robust to slightly skewed scans). Within a line, a gap wider than ~2 average character
    widths starts a new segment (a new column); this works both for PDF words and for OCR
    boxes, whose heights include padding.
    """
    lines: list[tuple[float, float, list[Box]]] = []
    for box in sorted(boxes, key=lambda b: (b.top + b.bottom) / 2):
        for index, (top, bottom, members) in enumerate(lines):
            if _vertical_overlap(box, top, bottom) >= 0.5:
                members.append(box)
                lines[index] = (min(top, box.top), max(bottom, box.bottom), members)
                break
        else:
            lines.append((box.top, box.bottom, [box]))
    result: list[PageLine] = []
    for _, _, members in sorted(lines, key=lambda ln: ln[0]):
        members.sort(key=lambda b: b.x0)
        segments: list[Box] = []
        for piece in members:
            last = segments[-1] if segments else None
            gap_limit = max(3.0, 2.0 * (_char_width(piece) + _char_width(last)) / 2) if last else 0
            if last and piece.x0 - last.x1 <= gap_limit:
                segments[-1] = Box(
                    f"{last.text} {piece.text}",
                    last.x0,
                    piece.x1,
                    min(last.top, piece.top),
                    max(last.bottom, piece.bottom),
                    (*(last.words or (last,)), piece),
                    _min_confidence(last, piece),
                )
            else:
                segments.append(piece)
        result.append(PageLine(page, width, height, tuple(segments)))
    return result


def _min_confidence(*boxes: Box) -> float | None:
    values = [b.confidence for b in boxes if b.confidence is not None]
    return min(values) if values else None


_CASE_JOIN = re.compile(r"(?<=[a-zа-яё.,;:])(?=[A-ZА-ЯЁ][a-zа-яё])")


def split_fused_words(box: Box) -> list[Box]:
    """OCR sometimes fuses neighbouring words ("рубРозница,"): split at a lower→upper case
    boundary, dividing the box proportionally to the characters."""
    parts = [p for p in _CASE_JOIN.split(box.text) if p]
    if len(parts) < 2:
        return [box]
    per_char = (box.x1 - box.x0) / max(1, len(box.text))
    result, x = [], box.x0
    for part in parts:
        width = per_char * len(part)
        result.append(Box(part, x, x + width, box.top, box.bottom, confidence=box.confidence))
        x += width
    return result


def assign(segment: Box, columns: list[Box], tabular: bool) -> list[tuple[int, Box]]:
    """In a table row (``tabular``: the line has several segments) a segment that spans
    several columns — e.g. tightly set headers "Опт, руб. Розница, руб." — is split into its
    words, each assigned on its own. A single-segment line (title, footer note) stays whole."""
    spanned = [c for c in columns if min(segment.x1, c.x1) - max(segment.x0, c.x0) > 0]
    if tabular and len(spanned) >= 2 and len(segment.words) > 1:
        return [(_column_of(word, columns), word) for word in segment.words]
    return [(_column_of(segment, columns), segment)]


def _column_of(segment: Box, columns: list[Box]) -> int:
    """Column whose header overlaps the segment most; nearest centre otherwise."""
    best, best_overlap = -1, 0.0
    for index, column in enumerate(columns):
        overlap = min(segment.x1, column.x1) - max(segment.x0, column.x0)
        if overlap > best_overlap:
            best, best_overlap = index, overlap
    if best >= 0:
        return best
    return min(range(len(columns)), key=lambda i: abs(columns[i].center - segment.center))


def _region(s: Box, line: PageLine) -> tuple[float, float, float, float]:
    def clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    x, y = clamp(s.x0 / line.width), clamp(s.top / line.height)
    w = clamp(s.x1 / line.width) - x
    h = clamp(s.bottom / line.height) - y
    return (round(x, 4), round(y, 4), round(max(w, 0.0), 4), round(max(h, 0.0), 4))


def build_rows(lines: list[PageLine], limits: Limits) -> tuple[list[Row], int]:
    """Rows of one continuous table; returns the rows and the number of columns found."""
    # Column layout: the first line with the most separate segments (usually the first
    # data row, whose values are spaced further apart than a tightly set header).
    header = max(lines[:HEADER_SCAN_LINES], key=lambda ln: len(ln.segments))
    columns = list(header.segments)
    rows: list[Row] = []
    for index, line in enumerate(lines, start=1):
        texts: dict[int, list[Box]] = {}
        if len(columns) >= 2:
            tabular = len(line.segments) >= 2
            for segment in line.segments:
                for column, piece in assign(segment, columns, tabular):
                    texts.setdefault(column, []).append(piece)
        else:
            texts[0] = list(line.segments)
        cells = []
        for column, segments in sorted(texts.items()):
            merged = Box(
                " ".join(s.text for s in segments),
                min(s.x0 for s in segments),
                max(s.x1 for s in segments),
                min(s.top for s in segments),
                max(s.bottom for s in segments),
                confidence=_min_confidence(*segments),
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
                    confidence=merged.confidence,
                )
            )
        rows.append(Row(index=index, cells=tuple(cells), page=line.page))
    return rows, len(columns)
