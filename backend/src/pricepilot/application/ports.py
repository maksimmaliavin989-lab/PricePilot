"""Ports implemented by infrastructure adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pricepilot.domain.audit import AuditEvent
from pricepilot.domain.catalog import Product, ProductIdentifier
from pricepilot.domain.documents import DocumentStatus, ProcessingRun, SourceDocument
from pricepilot.domain.extraction import ExtractedField, ExtractedLine, FieldRevision
from pricepilot.domain.suppliers import Supplier
from pricepilot.domain.workspaces import Workspace

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class PageCursor:
    """Keyset position: items strictly after ``(created_at, id)``."""

    created_at: datetime
    id: UUID


@dataclass(frozen=True, slots=True)
class PageRequest:
    limit: int = DEFAULT_PAGE_SIZE
    after: PageCursor | None = None


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_cursor: PageCursor | None


class WorkspaceRepository(Protocol):
    def add(self, workspace: Workspace) -> None: ...

    def get(self, workspace_id: UUID) -> Workspace | None: ...

    def list(self, page: PageRequest) -> Page[Workspace]: ...

    def update(self, workspace: Workspace, expected_version: int) -> bool:
        """Persist a new revision only if the stored version equals ``expected_version``."""
        ...


class SupplierRepository(Protocol):
    def add(self, supplier: Supplier) -> None: ...

    def get(self, supplier_id: UUID) -> Supplier | None: ...

    def list(
        self,
        workspace_id: UUID,
        page: PageRequest,
        *,
        query: str | None,
        include_archived: bool,
    ) -> Page[Supplier]: ...

    def update(self, supplier: Supplier, expected_version: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class LinePageRequest:
    """Lines are ordered by their position in the source, so the cursor is the ordinal."""

    limit: int = DEFAULT_PAGE_SIZE
    after_ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class LineWithFields:
    line: ExtractedLine
    fields: list[ExtractedField]


@dataclass(frozen=True, slots=True)
class LinePage:
    items: list[LineWithFields]
    next_ordinal: int | None


@dataclass(frozen=True, slots=True)
class RunCounts:
    lines: int
    fields: int
    lines_review_required: int


@dataclass(frozen=True, slots=True)
class DocumentFilter:
    status: DocumentStatus | None = None
    supplier_id: UUID | None = None


class DocumentRepository(Protocol):
    def add(self, document: SourceDocument) -> None: ...

    def get(self, document_id: UUID) -> SourceDocument | None: ...

    def find_by_hash(self, workspace_id: UUID, sha256: str) -> SourceDocument | None: ...

    def list(
        self, workspace_id: UUID, page: PageRequest, filters: DocumentFilter
    ) -> Page[SourceDocument]: ...

    def update(self, document: SourceDocument, expected_version: int) -> bool: ...


class RunRepository(Protocol):
    def add(self, run: ProcessingRun) -> None: ...

    def get(self, run_id: UUID) -> ProcessingRun | None: ...

    def find(self, document_id: UUID, pipeline: str, config_hash: str) -> ProcessingRun | None: ...

    def latest_for_document(self, document_id: UUID) -> ProcessingRun | None: ...

    def update(self, run: ProcessingRun, expected_version: int) -> bool: ...


class ExtractionRepository(Protocol):
    def add_line(self, line: ExtractedLine, fields: list[ExtractedField]) -> None: ...

    def counts(self, run_id: UUID) -> RunCounts: ...

    def list_lines(self, run_id: UUID, page: LinePageRequest) -> LinePage: ...

    def get_field(self, field_id: UUID) -> ExtractedField | None: ...

    def field_workspace(self, field_id: UUID) -> UUID | None: ...

    def update_field(self, field: ExtractedField, expected_revision: int) -> bool: ...

    def add_revision(self, revision: FieldRevision) -> None: ...

    def list_revisions(self, field_id: UUID) -> list[FieldRevision]: ...


class CatalogRepository(Protocol):
    def add(self, product: Product, identifiers: list[ProductIdentifier]) -> None: ...

    def get(self, product_id: UUID) -> tuple[Product, list[ProductIdentifier]] | None: ...

    def list(
        self, workspace_id: UUID, page: PageRequest, *, query: str | None
    ) -> Page[tuple[Product, list[ProductIdentifier]]]: ...


class AuditRepository(Protocol):
    def add(self, event: AuditEvent) -> None: ...

    def list_for_entity(self, entity_type: str, entity_id: UUID) -> list[AuditEvent]: ...


class UnitOfWork(Protocol):
    @property
    def workspaces(self) -> WorkspaceRepository: ...

    @property
    def suppliers(self) -> SupplierRepository: ...

    @property
    def documents(self) -> DocumentRepository: ...

    @property
    def runs(self) -> RunRepository: ...

    @property
    def extraction(self) -> ExtractionRepository: ...

    @property
    def catalog(self) -> CatalogRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
Clock = Callable[[], datetime]
CorrelationProvider = Callable[[], str | None]
