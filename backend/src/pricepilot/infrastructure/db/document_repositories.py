"""SQL adapters for Document Intake, processing runs and extraction results."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, func, select

from pricepilot.application.ports import (
    DocumentFilter,
    LinePage,
    LinePageRequest,
    LineWithFields,
    Page,
    PageRequest,
    RunCounts,
)
from pricepilot.domain.documents import (
    DocumentStatus,
    ImportSource,
    ProcessingRun,
    RetentionState,
    RunError,
    RunProgress,
    RunStatus,
    SourceDocument,
)
from pricepilot.domain.extraction import (
    ExtractedField,
    ExtractedLine,
    FieldRevision,
    LineStatus,
    Region,
    ReviewStatus,
    SourceLocation,
    ValueType,
)
from pricepilot.infrastructure.db.repositories import paginate, stamp_of, to_page
from pricepilot.infrastructure.db.tables import (
    extracted_field_revisions,
    extracted_fields,
    extracted_lines,
    processing_runs,
    source_documents,
)


def _document(row: Row[Any]) -> SourceDocument:
    return SourceDocument(
        id=row.id,
        workspace_id=row.workspace_id,
        supplier_id=row.supplier_id,
        original_name=row.original_name,
        detected_mime=row.detected_mime,
        extension=row.extension,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        storage_key=row.storage_key,
        import_source=ImportSource(row.import_source),
        status=DocumentStatus(row.status),
        retention_state=RetentionState(row.retention_state),
        stamp=stamp_of(row),
    )


def _run(row: Row[Any]) -> ProcessingRun:
    progress = (
        RunProgress(row.progress_stage, row.progress_completed or 0, row.progress_total)
        if row.progress_stage is not None
        else None
    )
    error = (
        RunError(row.error_code, bool(row.error_retryable)) if row.error_code is not None else None
    )
    return ProcessingRun(
        id=row.id,
        document_id=row.document_id,
        pipeline_version=row.pipeline_version,
        config_hash=row.config_hash,
        status=RunStatus(row.status),
        attempt=row.attempt,
        progress=progress,
        error=error,
        diagnostics_ref=row.diagnostics_ref,
        started_at=row.started_at,
        finished_at=row.finished_at,
        stamp=stamp_of(row),
    )


def _run_values(run: ProcessingRun) -> dict[str, Any]:
    return {
        "status": run.status.value,
        "attempt": run.attempt,
        "progress_stage": run.progress.stage if run.progress else None,
        "progress_completed": run.progress.completed if run.progress else None,
        "progress_total": run.progress.total if run.progress else None,
        "error_code": run.error.code if run.error else None,
        "error_retryable": run.error.retryable if run.error else None,
        "diagnostics_ref": run.diagnostics_ref,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "updated_at": run.stamp.updated_at,
        "version": run.stamp.version,
    }


def _line(row: Row[Any]) -> ExtractedLine:
    region = Region(**row.region_json) if row.region_json else None
    return ExtractedLine(
        id=row.id,
        run_id=row.run_id,
        ordinal=row.ordinal,
        raw_text=row.raw_text,
        location=SourceLocation(
            sheet=row.sheet,
            page=row.page,
            row=row.row_index,
            column=row.column_index,
            cell=row.cell,
            region=region,
        ),
        status=LineStatus(row.line_status),
        review_reasons=tuple(row.review_reasons_json or ()),
    )


def _field(row: Row[Any]) -> ExtractedField:
    return ExtractedField(
        id=row.id,
        line_id=row.line_id,
        field_key=row.field_key,
        value_type=ValueType(row.value_type),
        raw_value=row.raw_value,
        normalized_value=row.normalized_value_json,
        confidence=row.confidence,
        provenance=row.provenance_json,
        review_status=ReviewStatus(row.review_status),
        revision=row.revision,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
    )


def _revision(row: Row[Any]) -> FieldRevision:
    return FieldRevision(
        id=row.id,
        field_id=row.field_id,
        revision=row.revision,
        action=row.action,
        previous_value=row.previous_value_json,
        new_value=row.new_value_json,
        previous_status=ReviewStatus(row.previous_status),
        new_status=ReviewStatus(row.new_status),
        reason=row.reason,
        actor=row.actor_id,
        occurred_at=row.occurred_at,
    )


class SqlDocumentRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    def add(self, d: SourceDocument) -> None:
        self._c.execute(
            source_documents.insert().values(
                id=d.id,
                workspace_id=d.workspace_id,
                supplier_id=d.supplier_id,
                original_name=d.original_name,
                detected_mime=d.detected_mime,
                extension=d.extension,
                size_bytes=d.size_bytes,
                sha256=d.sha256,
                storage_key=d.storage_key,
                import_source=d.import_source.value,
                status=d.status.value,
                retention_state=d.retention_state.value,
                created_at=d.stamp.created_at,
                updated_at=d.stamp.updated_at,
                version=d.stamp.version,
            )
        )

    def get(self, document_id: UUID) -> SourceDocument | None:
        row = self._c.execute(
            select(source_documents).where(source_documents.c.id == document_id)
        ).first()
        return _document(row) if row else None

    def find_by_hash(self, workspace_id: UUID, sha256: str) -> SourceDocument | None:
        row = self._c.execute(
            select(source_documents).where(
                source_documents.c.workspace_id == workspace_id, source_documents.c.sha256 == sha256
            )
        ).first()
        return _document(row) if row else None

    def list(
        self, workspace_id: UUID, page: PageRequest, filters: DocumentFilter
    ) -> Page[SourceDocument]:
        stmt = select(source_documents).where(source_documents.c.workspace_id == workspace_id)
        if filters.status is not None:
            stmt = stmt.where(source_documents.c.status == filters.status.value)
        if filters.supplier_id is not None:
            stmt = stmt.where(source_documents.c.supplier_id == filters.supplier_id)
        rows = list(self._c.execute(paginate(source_documents, stmt, page)))
        return to_page(rows, page, _document)

    def update(self, d: SourceDocument, expected_version: int) -> bool:
        result = self._c.execute(
            source_documents.update()
            .where(source_documents.c.id == d.id, source_documents.c.version == expected_version)
            .values(
                supplier_id=d.supplier_id,
                status=d.status.value,
                retention_state=d.retention_state.value,
                updated_at=d.stamp.updated_at,
                version=d.stamp.version,
            )
        )
        return result.rowcount == 1


class SqlRunRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    def add(self, run: ProcessingRun) -> None:
        self._c.execute(
            processing_runs.insert().values(
                id=run.id,
                document_id=run.document_id,
                pipeline_version=run.pipeline_version,
                config_hash=run.config_hash,
                created_at=run.stamp.created_at,
                **_run_values(run),
            )
        )

    def get(self, run_id: UUID) -> ProcessingRun | None:
        row = self._c.execute(select(processing_runs).where(processing_runs.c.id == run_id)).first()
        return _run(row) if row else None

    def find(self, document_id: UUID, pipeline: str, config_hash: str) -> ProcessingRun | None:
        row = self._c.execute(
            select(processing_runs).where(
                processing_runs.c.document_id == document_id,
                processing_runs.c.pipeline_version == pipeline,
                processing_runs.c.config_hash == config_hash,
            )
        ).first()
        return _run(row) if row else None

    def latest_for_document(self, document_id: UUID) -> ProcessingRun | None:
        row = self._c.execute(
            select(processing_runs)
            .where(processing_runs.c.document_id == document_id)
            .order_by(processing_runs.c.created_at.desc(), processing_runs.c.id.desc())
            .limit(1)
        ).first()
        return _run(row) if row else None

    def list_by_status(self, statuses: frozenset[RunStatus]) -> list[ProcessingRun]:
        rows = self._c.execute(
            select(processing_runs)
            .where(processing_runs.c.status.in_([s.value for s in statuses]))
            .order_by(processing_runs.c.created_at, processing_runs.c.id)
        )
        return [_run(r) for r in rows]

    def update(self, run: ProcessingRun, expected_version: int) -> bool:
        result = self._c.execute(
            processing_runs.update()
            .where(processing_runs.c.id == run.id, processing_runs.c.version == expected_version)
            .values(**_run_values(run))
        )
        return result.rowcount == 1


class SqlExtractionRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    def add_line(self, line: ExtractedLine, fields: list[ExtractedField]) -> None:
        loc = line.location
        self._c.execute(
            extracted_lines.insert().values(
                id=line.id,
                run_id=line.run_id,
                ordinal=line.ordinal,
                raw_text=line.raw_text,
                sheet=loc.sheet,
                page=loc.page,
                row_index=loc.row,
                column_index=loc.column,
                cell=loc.cell,
                region_json=loc.region.to_json() if loc.region else None,
                line_status=line.status.value,
                review_reasons_json=list(line.review_reasons),
            )
        )
        if fields:
            self._c.execute(
                extracted_fields.insert(),
                [
                    {
                        "id": f.id,
                        "line_id": f.line_id,
                        "field_key": f.field_key,
                        "value_type": f.value_type.value,
                        "raw_value": f.raw_value,
                        "normalized_value_json": f.normalized_value,
                        "confidence": f.confidence,
                        "provenance_json": f.provenance,
                        "review_status": f.review_status.value,
                        "revision": f.revision,
                        "reviewed_by": f.reviewed_by,
                        "reviewed_at": f.reviewed_at,
                    }
                    for f in fields
                ],
            )

    def counts(self, run_id: UUID) -> RunCounts:
        lines = self._c.execute(
            select(
                func.count(),
                func.count().filter(extracted_lines.c.line_status == "review_required"),
            ).where(extracted_lines.c.run_id == run_id)
        ).one()
        fields = self._c.execute(
            select(func.count())
            .select_from(extracted_fields.join(extracted_lines))
            .where(extracted_lines.c.run_id == run_id)
        ).scalar_one()
        return RunCounts(lines=lines[0], fields=fields, lines_review_required=lines[1])

    def list_lines(self, run_id: UUID, page: LinePageRequest) -> LinePage:
        stmt = select(extracted_lines).where(extracted_lines.c.run_id == run_id)
        if page.after_ordinal is not None:
            stmt = stmt.where(extracted_lines.c.ordinal > page.after_ordinal)
        rows = list(self._c.execute(stmt.order_by(extracted_lines.c.ordinal).limit(page.limit + 1)))
        has_more = len(rows) > page.limit
        lines = [_line(r) for r in rows[: page.limit]]
        by_line: dict[UUID, list[ExtractedField]] = {line.id: [] for line in lines}
        if lines:
            for row in self._c.execute(
                select(extracted_fields)
                .where(extracted_fields.c.line_id.in_(list(by_line)))
                .order_by(extracted_fields.c.field_key)
            ):
                by_line[row.line_id].append(_field(row))
        return LinePage(
            items=[LineWithFields(line, by_line[line.id]) for line in lines],
            next_ordinal=lines[-1].ordinal if has_more and lines else None,
        )

    def get_field(self, field_id: UUID) -> ExtractedField | None:
        row = self._c.execute(
            select(extracted_fields).where(extracted_fields.c.id == field_id)
        ).first()
        return _field(row) if row else None

    def field_workspace(self, field_id: UUID) -> UUID | None:
        value: UUID | None = self._c.execute(
            select(source_documents.c.workspace_id)
            .select_from(
                extracted_fields.join(extracted_lines).join(processing_runs).join(source_documents)
            )
            .where(extracted_fields.c.id == field_id)
        ).scalar_one_or_none()
        return value

    def update_field(self, f: ExtractedField, expected_revision: int) -> bool:
        result = self._c.execute(
            extracted_fields.update()
            .where(extracted_fields.c.id == f.id, extracted_fields.c.revision == expected_revision)
            .values(
                normalized_value_json=f.normalized_value,
                review_status=f.review_status.value,
                revision=f.revision,
                reviewed_by=f.reviewed_by,
                reviewed_at=f.reviewed_at,
            )
        )
        return result.rowcount == 1

    def add_revision(self, r: FieldRevision) -> None:
        self._c.execute(
            extracted_field_revisions.insert().values(
                id=r.id,
                field_id=r.field_id,
                revision=r.revision,
                action=r.action,
                previous_value_json=r.previous_value,
                new_value_json=r.new_value,
                previous_status=r.previous_status.value,
                new_status=r.new_status.value,
                reason=r.reason,
                actor_id=r.actor,
                occurred_at=r.occurred_at,
            )
        )

    def list_revisions(self, field_id: UUID) -> list[FieldRevision]:
        rows = self._c.execute(
            select(extracted_field_revisions)
            .where(extracted_field_revisions.c.field_id == field_id)
            .order_by(extracted_field_revisions.c.revision)
        )
        return [_revision(r) for r in rows]
