"""Canonical, format-neutral document model produced by DIP adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class FileKind(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"
    XLS = "xls"
    DOCX = "docx"
    DOC = "doc"
    PDF = "pdf"
    JPEG = "jpeg"
    PNG = "png"
    TIFF = "tiff"
    HEIC = "heic"
    UNKNOWN = "unknown"


MIME_TYPES: dict[FileKind, str] = {
    FileKind.CSV: "text/csv",
    FileKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FileKind.XLS: "application/vnd.ms-excel",
    FileKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FileKind.DOC: "application/msword",
    FileKind.PDF: "application/pdf",
    FileKind.JPEG: "image/jpeg",
    FileKind.PNG: "image/png",
    FileKind.TIFF: "image/tiff",
    FileKind.HEIC: "image/heic",
    FileKind.UNKNOWN: "application/octet-stream",
}


def column_letter(index: int) -> str:
    """1 → A, 27 → AA."""
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


@dataclass(frozen=True, slots=True)
class Cell:
    """One non-empty source cell. ``text`` is what the source shows (or its canonical form
    for typed spreadsheet values); ``number`` is set only when the source itself stores a
    number — text such as ``"1 234,50"`` stays text and is interpreted later."""

    row: int
    column: int
    text: str
    number: Decimal | None = None
    formula: str | None = None
    is_date: bool = False
    # Paged formats (PDF/OCR): page number and bounding box normalised to the page (0..1,
    # top-left origin) as (x, y, width, height). Spreadsheet cells leave both empty.
    page: int | None = None
    region: tuple[float, float, float, float] | None = None
    # Recognised text (OCR): engine confidence 0..1 (lowest of the merged words). None for
    # text that was read, not recognised.
    confidence: float | None = None

    @property
    def ref(self) -> str:
        return f"{column_letter(self.column)}{self.row}"

    @property
    def has_cell_address(self) -> bool:
        """Spreadsheet-like cells have an A1 address; text extracted from a page does not."""
        return self.page is None


@dataclass(frozen=True, slots=True)
class Row:
    index: int  # 1-based row number in the source sheet/file (PDF: line number in the document)
    cells: tuple[Cell, ...]
    page: int | None = None

    def cell(self, column: int) -> Cell | None:
        for c in self.cells:
            if c.column == column:
                return c
        return None


@dataclass(frozen=True, slots=True)
class Sheet:
    name: str | None  # None for formats without sheets (CSV)
    rows: tuple[Row, ...]


@dataclass(frozen=True, slots=True)
class TableDocument:
    sheets: tuple[Sheet, ...]
    diagnostics: dict[str, str] = field(default_factory=dict)  # safe metadata only


class ParseError(Exception):
    """A document-level failure with a stable code from TECHNICAL_SPEC/ERROR_HANDLING.md."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
