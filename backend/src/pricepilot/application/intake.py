"""Use cases: importing a file and executing a processing run.

Import:  stream → hash/size limit → sniff → immutable store → register (dedup) → queue run.
Run:     validating (integrity) → parsing (adapter) → extracting (price lines) →
         validating_result → review_required | completed; any failure → ``failed`` with a
         stable code. A failed or unsupported file never looks processed, and one file's
         failure never stops the others.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pricepilot.application.documents import DocumentService, ProcessingService
from pricepilot.application.errors import InvalidStateError, NotFoundError
from pricepilot.application.ports import UnitOfWorkFactory
from pricepilot.dip.adapters.registry import PIPELINE_VERSION, PLANNED_KINDS, ParserRegistry
from pricepilot.dip.limits import Limits
from pricepilot.dip.model import FileKind, ParseError
from pricepilot.dip.sniff import PERMANENTLY_UNSUPPORTED, SniffResult, classify
from pricepilot.domain.documents import (
    ACTIVE_RUN_STATES,
    ImportSource,
    ProcessingRun,
    RunStatus,
    SourceDocument,
    document_file_name,
)
from pricepilot.domain.price_lines import ExtractionContext, extract_price_lines

logger = logging.getLogger("pricepilot.pipeline")
MAX_AUTO_ATTEMPTS = 3


class OriginalStoreError(Exception):
    """Raised by store adapters when an original is missing or fails its hash check."""


@dataclass(frozen=True, slots=True)
class IncomingFile:
    """A fully received upload in the store's quarantine area."""

    path: Path
    sha256: str
    size: int


class OriginalStore(Protocol):
    def commit(self, incoming: IncomingFile) -> str: ...

    def discard(self, incoming: IncomingFile) -> None: ...

    def read(self, key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ImportProblem:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    document: SourceDocument
    run: ProcessingRun | None
    duplicate: bool
    problem: ImportProblem | None


Enqueue = Callable[[UUID], None]


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""


class ImportService:
    def __init__(
        self,
        documents: DocumentService,
        processing: ProcessingService,
        store: OriginalStore,
        registry: ParserRegistry,
        limits: Limits,
        enqueue: Enqueue,
    ) -> None:
        self._documents = documents
        self._processing = processing
        self._store = store
        self._registry = registry
        self._limits = limits
        self._enqueue = enqueue

    def import_file(
        self,
        *,
        workspace_id: UUID,
        supplier_id: UUID | None,
        filename: str,
        incoming: IncomingFile,
        source: ImportSource,
    ) -> ImportResult:
        filename = document_file_name(filename)
        self._documents.check_target(workspace_id, supplier_id)
        sniff: SniffResult = classify(incoming.path, _extension(filename), self._limits)
        extension = _extension(filename) or "bin"
        if not extension.isalnum() or len(extension) > 10:
            extension = "bin"
        key = self._store.commit(incoming)  # original is preserved whatever happens next
        supported = sniff.problem != "UNSUPPORTED_FORMAT" and sniff.kind not in (
            PERMANENTLY_UNSUPPORTED - {FileKind.UNKNOWN}
        )
        registration = self._documents.register(
            workspace_id=workspace_id,
            supplier_id=supplier_id,
            original_name=filename,
            detected_mime=sniff.mime,
            extension=extension,
            size_bytes=incoming.size,
            sha256=incoming.sha256,
            storage_key=key,
            import_source=source,
            supported=supported,
        )
        document = registration.document
        if registration.duplicate:
            return ImportResult(document, None, duplicate=True, problem=None)
        if not supported:
            return ImportResult(
                document,
                None,
                False,
                ImportProblem(sniff.problem or "UNSUPPORTED_FORMAT", sniff.message or ""),
            )

        run, _ = self._processing.start(
            document.id, PIPELINE_VERSION, self._registry.config_hash(self._limits)
        )
        problem: ImportProblem | None = None
        if sniff.problem is not None:
            problem = ImportProblem(sniff.problem, sniff.message or "")
        elif self._registry.for_kind(sniff.kind) is None:
            label = PLANNED_KINDS.get(sniff.kind, sniff.kind.value.upper())
            problem = ImportProblem(
                "UNSUPPORTED_FORMAT",
                f"Обработка {label} ещё не реализована. Оригинал сохранён; его можно будет "
                "обработать после обновления.",
            )
        if problem is not None:
            self._processing.advance(run.id, RunStatus.VALIDATING)
            run = self._processing.fail(run.id, problem.code, retryable=False)
        else:
            self._enqueue(run.id)
        return ImportResult(self._documents.get(document.id), run, False, problem)

    def reprocess(self, document_id: UUID) -> tuple[ProcessingRun, bool]:
        """Start (idempotently) a run with the current pipeline version and config."""
        document = self._documents.get(document_id)
        kind, parser = self._registry.for_mime(document.detected_mime)
        if parser is None or kind is FileKind.UNKNOWN:
            raise InvalidStateError("source_document", document.status, "processing")
        run, created = self._processing.start(
            document_id, PIPELINE_VERSION, self._registry.config_hash(self._limits)
        )
        if created:
            self._enqueue(run.id)
        return run, created


class PipelineRunner:
    """Executes one queued run. Safe to call again for the same run (idempotent steps)."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        documents: DocumentService,
        processing: ProcessingService,
        store: OriginalStore,
        registry: ParserRegistry,
        limits: Limits,
    ) -> None:
        self._uow = uow
        self._documents = documents
        self._processing = processing
        self._store = store
        self._registry = registry
        self._limits = limits

    def _fallback_currency(self, document: SourceDocument) -> str:
        with self._uow() as uow:
            if document.supplier_id is not None:
                supplier = uow.suppliers.get(document.supplier_id)
                if supplier is not None:
                    return supplier.default_currency
            workspace = uow.workspaces.get(document.workspace_id)
        if workspace is None:  # pragma: no cover - FK guarantees it
            raise NotFoundError("workspace")
        return workspace.base_currency

    def execute(self, run_id: UUID) -> RunStatus | None:
        try:
            view = self._processing.get(run_id)
        except NotFoundError:
            return None
        if view.run.status is not RunStatus.QUEUED:
            return view.run.status  # cancelled, finished or taken by someone else
        try:
            return self._execute(view.run)
        except InvalidStateError:
            # The run was cancelled (or otherwise moved) concurrently: stop cooperatively.
            return self._processing.get(run_id).run.status
        except ParseError as exc:
            return self._fail(run_id, exc.code, retryable=exc.retryable)
        except Exception as exc:
            logger.error("processing failed", exc_info=exc, extra={"error_code": "INTERNAL_ERROR"})
            return self._fail(run_id, "INTERNAL_ERROR", retryable=True)

    def _fail(self, run_id: UUID, code: str, *, retryable: bool) -> RunStatus:
        run = self._processing.get(run_id).run
        if run.status in ACTIVE_RUN_STATES:
            return self._processing.fail(run_id, code, retryable=retryable).status
        return run.status

    def _execute(self, run: ProcessingRun) -> RunStatus:
        document = self._documents.get(run.document_id)
        self._processing.advance(run.id, RunStatus.VALIDATING)
        try:
            data = self._store.read(document.storage_key)
        except OriginalStoreError as exc:
            raise ParseError("STORAGE_ERROR", "Оригинал недоступен или повреждён.") from exc
        kind, parser = self._registry.for_mime(document.detected_mime)
        if parser is None:
            raise ParseError("UNSUPPORTED_FORMAT", f"Нет обработчика для {kind.value}.")

        self._processing.advance(run.id, RunStatus.PARSING)
        table = parser.parse(data, self._limits)

        self._processing.advance(run.id, RunStatus.EXTRACTING)
        if self._processing.get(run.id).counts.lines == 0:
            lines = extract_price_lines(
                table, ExtractionContext(fallback_currency=self._fallback_currency(document))
            )
            self._processing.record_lines(
                run.id, lines, extractor=parser.name, extractor_version=parser.version
            )

        self._processing.advance(run.id, RunStatus.VALIDATING_RESULT)
        counts = self._processing.get(run.id).counts
        if counts.lines == 0:
            raise ParseError(
                "NO_TABLE_FOUND",
                "Файл прочитан, но таблица товаров не найдена (нет заголовков "
                "«Наименование» и «Цена»).",
            )
        final = RunStatus.REVIEW_REQUIRED if counts.lines_review_required else RunStatus.COMPLETED
        return self._processing.advance(run.id, final).status

    def recover(self) -> list[UUID]:
        """After a crash: interrupted runs fail as retryable and are re-queued (bounded)."""
        requeued: list[UUID] = []
        for run in self._processing.runs_in(ACTIVE_RUN_STATES - {RunStatus.QUEUED}):
            failed = self._processing.fail(run.id, "INTERRUPTED", retryable=True)
            if failed.attempt < MAX_AUTO_ATTEMPTS:
                requeued.append(self._processing.retry(run.id).id)
        requeued.extend(r.id for r in self._processing.runs_in(frozenset({RunStatus.QUEUED})))
        return list(dict.fromkeys(requeued))
