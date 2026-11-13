"""Use cases: document registration, processing-run lifecycle and extraction persistence.

HTTP upload/parsing arrives in TASK_003; these services are the contract it will call.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pricepilot.application.errors import (
    DuplicateExtractionError,
    InvalidStateError,
    NotFoundError,
    VersionConflictError,
)
from pricepilot.application.ports import (
    MAX_PAGE_SIZE,
    Clock,
    CorrelationProvider,
    DocumentFilter,
    LinePage,
    LinePageRequest,
    Page,
    PageRequest,
    RunCounts,
    UnitOfWork,
    UnitOfWorkFactory,
)
from pricepilot.domain.audit import LOCAL_ACTOR, AuditEvent
from pricepilot.domain.common import utc_now
from pricepilot.domain.documents import (
    EXTRACTION_WRITABLE,
    DocumentStatus,
    ImportSource,
    InvalidTransitionError,
    ProcessingRun,
    RunProgress,
    RunStatus,
    SourceDocument,
    document_status_for_run,
)
from pricepilot.domain.extraction import (
    ExtractedField,
    ExtractedLine,
    NewLine,
    Provenance,
)


@dataclass(frozen=True, slots=True)
class Registration:
    document: SourceDocument
    duplicate: bool


@dataclass(frozen=True, slots=True)
class RunView:
    run: ProcessingRun
    counts: RunCounts


def _no_correlation() -> str | None:
    return None


class DocumentService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock = utc_now,
        correlation: CorrelationProvider = _no_correlation,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._correlation = correlation

    def register(
        self,
        *,
        workspace_id: UUID,
        supplier_id: UUID | None,
        original_name: str,
        detected_mime: str,
        extension: str,
        size_bytes: int,
        sha256: str,
        storage_key: str,
        import_source: ImportSource,
        supported: bool,
    ) -> Registration:
        """Register an already stored original. Same bytes in the same workspace → the existing
        document is returned (dedup reference) instead of a second record."""
        document = SourceDocument.register(
            workspace_id=workspace_id,
            supplier_id=supplier_id,
            original_name=original_name,
            detected_mime=detected_mime,
            extension=extension,
            size_bytes=size_bytes,
            sha256=sha256,
            storage_key=storage_key,
            import_source=import_source,
            supported=supported,
            now=self._clock(),
        )
        with self._uow() as uow:
            if uow.workspaces.get(workspace_id) is None:
                raise NotFoundError("workspace")
            if supplier_id is not None:
                supplier = uow.suppliers.get(supplier_id)
                if supplier is None or supplier.workspace_id != workspace_id:
                    raise NotFoundError("supplier")
            existing = uow.documents.find_by_hash(workspace_id, document.sha256)
            if existing is not None:
                return Registration(existing, duplicate=True)
            uow.documents.add(document)
            uow.audit.add(
                AuditEvent.record(
                    workspace_id=workspace_id,
                    actor_id=LOCAL_ACTOR,
                    entity_type="source_document",
                    entity_id=document.id,
                    action="register",
                    before=None,
                    after={"status": document.status.value, "sha256": document.sha256},
                    now=document.stamp.created_at,
                    correlation_id=self._correlation(),
                )
            )
            uow.commit()
        return Registration(document, duplicate=False)

    def check_target(self, workspace_id: UUID, supplier_id: UUID | None) -> None:
        """Validate the destination before any bytes are stored."""
        with self._uow() as uow:
            if uow.workspaces.get(workspace_id) is None:
                raise NotFoundError("workspace")
            if supplier_id is not None:
                supplier = uow.suppliers.get(supplier_id)
                if supplier is None or supplier.workspace_id != workspace_id:
                    raise NotFoundError("supplier")

    def get(self, document_id: UUID) -> SourceDocument:
        with self._uow() as uow:
            document = uow.documents.get(document_id)
        if document is None:
            raise NotFoundError("source_document")
        return document

    def latest_run_id(self, document_id: UUID) -> UUID | None:
        with self._uow() as uow:
            run = uow.runs.latest_for_document(document_id)
        return run.id if run else None

    def list(
        self, workspace_id: UUID, page: PageRequest, filters: DocumentFilter
    ) -> Page[SourceDocument]:
        bounded = PageRequest(limit=max(1, min(page.limit, MAX_PAGE_SIZE)), after=page.after)
        with self._uow() as uow:
            if uow.workspaces.get(workspace_id) is None:
                raise NotFoundError("workspace")
            return uow.documents.list(workspace_id, bounded, filters)


class ProcessingService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock = utc_now,
        correlation: CorrelationProvider = _no_correlation,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._correlation = correlation

    # --- helpers --------------------------------------------------------------------------

    @staticmethod
    def _load(uow: UnitOfWork, run_id: UUID) -> tuple[ProcessingRun, SourceDocument]:
        run = uow.runs.get(run_id)
        if run is None:
            raise NotFoundError("processing_run")
        document = uow.documents.get(run.document_id)
        if document is None:  # pragma: no cover - FK guarantees it
            raise NotFoundError("source_document")
        return run, document

    def _save(
        self, uow: UnitOfWork, before: ProcessingRun, after: ProcessingRun, doc: SourceDocument
    ) -> None:
        if not uow.runs.update(after, before.stamp.version):
            latest = uow.runs.get(before.id)
            raise VersionConflictError("processing_run", latest.stamp.version if latest else 0)
        status = document_status_for_run(after.status)
        if status is not doc.status:
            updated = doc.with_status(status, self._clock())
            if not uow.documents.update(updated, doc.stamp.version):
                raise VersionConflictError("source_document", doc.stamp.version)

    def _audit(self, uow: UnitOfWork, doc: SourceDocument, run: ProcessingRun, action: str) -> None:
        uow.audit.add(
            AuditEvent.record(
                workspace_id=doc.workspace_id,
                actor_id=LOCAL_ACTOR,
                entity_type="processing_run",
                entity_id=run.id,
                action=action,
                before=None,
                after={"status": run.status.value, "attempt": run.attempt},
                now=self._clock(),
                correlation_id=self._correlation(),
            )
        )

    def _transition(
        self,
        run_id: UUID,
        change: Callable[[ProcessingRun, datetime], ProcessingRun],
        audit_action: str | None = None,
    ) -> ProcessingRun:
        with self._uow() as uow:
            run, document = self._load(uow, run_id)
            try:
                updated = change(run, self._clock())
            except InvalidTransitionError as exc:
                raise InvalidStateError("processing_run", exc.current, exc.target) from exc
            self._save(uow, run, updated, document)
            if audit_action is not None:
                self._audit(uow, document, updated, audit_action)
            uow.commit()
        return updated

    # --- use cases ------------------------------------------------------------------------

    def start(
        self, document_id: UUID, pipeline: str, config_hash: str
    ) -> tuple[ProcessingRun, bool]:
        """Queue a run. Idempotent per (document, pipeline version, config hash)."""
        with self._uow() as uow:
            document = uow.documents.get(document_id)
            if document is None:
                raise NotFoundError("source_document")
            candidate = ProcessingRun.queue(
                document_id=document_id,
                pipeline=pipeline,
                config_hash=config_hash,
                now=self._clock(),
            )
            existing = uow.runs.find(document_id, candidate.pipeline_version, candidate.config_hash)
            if existing is not None:
                return existing, False
            if document.status is DocumentStatus.UNSUPPORTED:
                raise InvalidStateError("source_document", document.status, "processing")
            uow.runs.add(candidate)
            updated = document.with_status(document_status_for_run(candidate.status), self._clock())
            if not uow.documents.update(updated, document.stamp.version):
                raise VersionConflictError("source_document", document.stamp.version)
            uow.commit()
        return candidate, True

    def advance(
        self, run_id: UUID, target: RunStatus, progress: RunProgress | None = None
    ) -> ProcessingRun:
        return self._transition(
            run_id, lambda r, now: r.advance(target, now=now, progress=progress)
        )

    def fail(self, run_id: UUID, code: str, *, retryable: bool) -> ProcessingRun:
        return self._transition(
            run_id, lambda r, now: r.fail(code=code, retryable=retryable, now=now)
        )

    def cancel(self, run_id: UUID) -> ProcessingRun:
        return self._transition(run_id, lambda r, now: r.cancel(now=now), "cancel")

    def retry(self, run_id: UUID) -> ProcessingRun:
        return self._transition(run_id, lambda r, now: r.retry(now=now), "retry")

    def record_lines(
        self,
        run_id: UUID,
        lines: Sequence[NewLine],
        *,
        extractor: str,
        extractor_version: str,
    ) -> RunCounts:
        """Persist parser output once per run (replays are rejected, never duplicated)."""
        with self._uow() as uow:
            run, document = self._load(uow, run_id)
            if run.status not in EXTRACTION_WRITABLE:
                raise InvalidStateError("processing_run", run.status, "extracting")
            if uow.extraction.counts(run_id).lines:
                raise DuplicateExtractionError
            for ordinal, new_line in enumerate(lines, start=1):
                line = ExtractedLine.extracted(
                    run_id=run_id,
                    ordinal=ordinal,
                    raw_text=new_line.raw_text,
                    location=new_line.location,
                    review_reasons=new_line.review_reasons,
                )
                fields = [
                    ExtractedField.extracted(
                        line_id=line.id,
                        key=f.key,
                        value_type=f.value_type,
                        raw_value=f.raw_value,
                        normalized=f.normalized,
                        confidence=f.confidence,
                        provenance=Provenance(
                            source_document_id=document.id,
                            location=f.location,
                            extractor=extractor,
                            extractor_version=extractor_version,
                            pipeline_version=run.pipeline_version,
                            method=f.method,
                            header=f.header,
                            formula=f.formula,
                        ),
                    )
                    for f in new_line.fields
                ]
                uow.extraction.add_line(line, fields)
            counts = uow.extraction.counts(run_id)
            uow.commit()
        return counts

    def runs_in(self, statuses: frozenset[RunStatus]) -> list[ProcessingRun]:
        with self._uow() as uow:
            return uow.runs.list_by_status(statuses)

    def get(self, run_id: UUID) -> RunView:
        with self._uow() as uow:
            run, _ = self._load(uow, run_id)
            return RunView(run=run, counts=uow.extraction.counts(run_id))

    def lines(self, document_id: UUID, run_id: UUID | None, page: LinePageRequest) -> LinePage:
        bounded = LinePageRequest(
            limit=max(1, min(page.limit, MAX_PAGE_SIZE)), after_ordinal=page.after_ordinal
        )
        with self._uow() as uow:
            if uow.documents.get(document_id) is None:
                raise NotFoundError("source_document")
            run = uow.runs.get(run_id) if run_id else uow.runs.latest_for_document(document_id)
            if run is None or run.document_id != document_id:
                if run_id is None:
                    return LinePage(items=[], next_ordinal=None)
                raise NotFoundError("processing_run")
            return uow.extraction.list_lines(run.id, bounded)
