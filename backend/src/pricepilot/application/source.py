"""Use case: show the original around an extracted line (FR-07 "source context").

The stored original is re-read through the same DIP adapter that produced the lines, so cell
references in provenance and the rows shown here always agree. Nothing is written; results are
cached per content hash because the original is immutable.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

from pricepilot.application.documents import DocumentService
from pricepilot.application.errors import InvalidStateError, NotFoundError
from pricepilot.application.intake import OriginalStore, OriginalStoreError
from pricepilot.dip.adapters.registry import ParserRegistry
from pricepilot.dip.limits import Limits
from pricepilot.dip.model import ParseError, Row, TableDocument
from pricepilot.domain.common import DomainValidationError

MAX_WINDOW = 200
_CACHE_SIZE = 4


@dataclass(frozen=True, slots=True)
class SourceWindow:
    sheet: str | None
    sheets: list[str | None]
    first_row: int
    last_row: int
    total_rows: int  # last non-empty row number of the sheet
    rows: list[Row]


class SourceService:
    def __init__(
        self,
        documents: DocumentService,
        store: OriginalStore,
        registry: ParserRegistry,
        limits: Limits,
    ) -> None:
        self._documents = documents
        self._store = store
        self._registry = registry
        self._limits = limits
        self._cache: OrderedDict[str, TableDocument] = OrderedDict()
        self._lock = threading.Lock()

    def _table(self, sha256: str, storage_key: str, mime: str) -> TableDocument:
        with self._lock:
            cached = self._cache.get(sha256)
            if cached is not None:
                self._cache.move_to_end(sha256)
                return cached
        kind, parser = self._registry.for_mime(mime)
        if parser is None:
            raise InvalidStateError("source_document", kind.value, "source_view")
        try:
            table = parser.parse(self._store.read(storage_key), self._limits)
        except (ParseError, OriginalStoreError) as exc:
            raise InvalidStateError("source_document", "unreadable", "source_view") from exc
        with self._lock:
            self._cache[sha256] = table
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
        return table

    def rows(
        self, document_id: UUID, *, sheet: str | None, first_row: int, last_row: int
    ) -> SourceWindow:
        if first_row < 1 or last_row < first_row:
            raise DomainValidationError("from", "Некорректный диапазон строк.")
        if last_row - first_row + 1 > MAX_WINDOW:
            raise DomainValidationError("to", f"Не более {MAX_WINDOW} строк за запрос.")
        document = self._documents.get(document_id)
        table = self._table(document.sha256, document.storage_key, document.detected_mime)
        names = [s.name for s in table.sheets]
        selected = next((s for s in table.sheets if s.name == sheet), None)
        if selected is None:
            if sheet is not None or not table.sheets:
                raise NotFoundError("sheet")
            selected = table.sheets[0]
        rows = [r for r in selected.rows if first_row <= r.index <= last_row]
        total = selected.rows[-1].index if selected.rows else 0
        return SourceWindow(selected.name, names, first_row, last_row, total, rows)
