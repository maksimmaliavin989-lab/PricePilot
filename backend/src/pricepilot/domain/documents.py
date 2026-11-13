"""Document Intake + processing-run lifecycle (DIP platform layer, product-neutral).

The run state machine follows ARCHITECTURE/PROCESSING_RUNTIME.md:
``queued → validating → parsing → ocr? → extracting → validating_result →
review_required | completed`` with side states ``failed``, ``cancelled``, ``retry_wait``.
A document is never reported as processed unless one of its runs actually finished.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pricepilot.domain.common import (
    AuditStamp,
    DomainValidationError,
    ensure_utc,
    new_id,
    optional_text,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,10}$")
_PIPELINE_RE = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class InvalidTransitionError(Exception):
    def __init__(self, entity: str, current: str, target: str) -> None:
        super().__init__(f"{entity}: {current} → {target} is not allowed")
        self.entity = entity
        self.current = current
        self.target = target


class ImportSource(StrEnum):
    FILE_PICKER = "file_picker"
    DRAG_DROP = "drag_drop"
    FOLDER = "folder"


class RetentionState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class DocumentStatus(StrEnum):
    STORED = "stored"  # original saved, not processed yet
    PROCESSING = "processing"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"  # e.g. legacy DOC: never retried automatically


class RunStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    PARSING = "parsing"
    OCR = "ocr"
    EXTRACTING = "extracting"
    VALIDATING_RESULT = "validating_result"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY_WAIT = "retry_wait"


ACTIVE_RUN_STATES = frozenset(
    {
        RunStatus.QUEUED,
        RunStatus.VALIDATING,
        RunStatus.PARSING,
        RunStatus.OCR,
        RunStatus.EXTRACTING,
        RunStatus.VALIDATING_RESULT,
        RunStatus.RETRY_WAIT,
    }
)
TERMINAL_RUN_STATES = frozenset(
    {RunStatus.REVIEW_REQUIRED, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)
_FORWARD = {
    RunStatus.QUEUED: {RunStatus.VALIDATING},
    RunStatus.VALIDATING: {RunStatus.PARSING},
    RunStatus.PARSING: {RunStatus.OCR, RunStatus.EXTRACTING},
    RunStatus.OCR: {RunStatus.EXTRACTING},
    RunStatus.EXTRACTING: {RunStatus.VALIDATING_RESULT},
    RunStatus.VALIDATING_RESULT: {RunStatus.REVIEW_REQUIRED, RunStatus.COMPLETED},
    RunStatus.RETRY_WAIT: {RunStatus.QUEUED},
}
# Lines may be written only while extraction is in progress.
EXTRACTION_WRITABLE = frozenset({RunStatus.EXTRACTING})


def run_transition_allowed(current: RunStatus, target: RunStatus) -> bool:
    if target in _FORWARD.get(current, set()):
        return True
    if target in {RunStatus.FAILED, RunStatus.CANCELLED} and current in ACTIVE_RUN_STATES:
        return True
    return current is RunStatus.FAILED and target is RunStatus.RETRY_WAIT


def document_status_for_run(status: RunStatus) -> DocumentStatus:
    if status in ACTIVE_RUN_STATES:
        return DocumentStatus.PROCESSING
    return {
        RunStatus.REVIEW_REQUIRED: DocumentStatus.REVIEW_REQUIRED,
        RunStatus.COMPLETED: DocumentStatus.COMPLETED,
        RunStatus.FAILED: DocumentStatus.FAILED,
        # A cancelled run leaves the original stored but unprocessed.
        RunStatus.CANCELLED: DocumentStatus.STORED,
    }[status]


def sha256_hex(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise DomainValidationError("sha256", "SHA-256: 64 шестнадцатеричных символа.")
    return normalized


def file_extension(value: str) -> str:
    normalized = value.strip().lower().lstrip(".")
    if not _EXTENSION_RE.fullmatch(normalized):
        raise DomainValidationError("extension", "Некорректное расширение файла.")
    return normalized


def pipeline_version(value: str) -> str:
    if not _PIPELINE_RE.fullmatch(value):
        raise DomainValidationError("pipeline_version", "Некорректная версия pipeline.")
    return value


def error_code(value: str) -> str:
    if not _ERROR_CODE_RE.fullmatch(value):
        raise DomainValidationError("error_code", "Код ошибки должен быть UPPER_SNAKE_CASE.")
    return value


def document_file_name(value: str) -> str:
    """Original file name without any path component (never used as a filesystem path)."""
    name = optional_text(value, "original_name", 255)
    if name is None or "/" in name or "\\" in name or name in {".", ".."}:
        raise DomainValidationError("original_name", "Имя файла без пути обязательно.")
    return name


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Metadata of an immutable original. Bytes live in the file store under ``storage_key``."""

    id: UUID
    workspace_id: UUID
    supplier_id: UUID | None
    original_name: str
    detected_mime: str
    extension: str
    size_bytes: int
    sha256: str
    storage_key: str
    import_source: ImportSource
    status: DocumentStatus
    retention_state: RetentionState
    stamp: AuditStamp

    @staticmethod
    def register(
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
        now: datetime,
    ) -> SourceDocument:
        name = document_file_name(original_name)
        mime = optional_text(detected_mime, "detected_mime", 127)
        if mime is None:
            raise DomainValidationError("detected_mime", "MIME-тип обязателен.")
        if size_bytes < 0:
            raise DomainValidationError("size_bytes", "Размер не может быть отрицательным.")
        key = optional_text(storage_key, "storage_key", 255)
        if key is None:
            raise DomainValidationError("storage_key", "Ключ хранилища обязателен.")
        return SourceDocument(
            id=new_id(),
            workspace_id=workspace_id,
            supplier_id=supplier_id,
            original_name=name,
            detected_mime=mime,
            extension=file_extension(extension),
            size_bytes=size_bytes,
            sha256=sha256_hex(sha256),
            storage_key=key,
            import_source=import_source,
            status=DocumentStatus.STORED if supported else DocumentStatus.UNSUPPORTED,
            retention_state=RetentionState.ACTIVE,
            stamp=AuditStamp.initial(now),
        )

    def with_status(self, status: DocumentStatus, now: datetime) -> SourceDocument:
        if self.status is DocumentStatus.UNSUPPORTED and status is not DocumentStatus.UNSUPPORTED:
            raise InvalidTransitionError("document", self.status, status)
        return replace(self, status=status, stamp=self.stamp.bumped(now))


@dataclass(frozen=True, slots=True)
class RunProgress:
    stage: str
    completed: int
    total: int | None

    def __post_init__(self) -> None:
        if self.completed < 0 or (self.total is not None and not 0 <= self.completed <= self.total):
            raise DomainValidationError("progress", "Некорректный прогресс.")


@dataclass(frozen=True, slots=True)
class RunError:
    code: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class ProcessingRun:
    """One extraction attempt of a document with a fixed pipeline version and config.

    ``(document_id, pipeline_version, config_hash)`` is the idempotency key: a new
    pipeline version or config creates a new run instead of overwriting old results.
    """

    id: UUID
    document_id: UUID
    pipeline_version: str
    config_hash: str
    status: RunStatus
    attempt: int
    progress: RunProgress | None
    error: RunError | None
    diagnostics_ref: str | None
    started_at: datetime | None
    finished_at: datetime | None
    stamp: AuditStamp

    @staticmethod
    def queue(
        *, document_id: UUID, pipeline: str, config_hash: str, now: datetime
    ) -> ProcessingRun:
        return ProcessingRun(
            id=new_id(),
            document_id=document_id,
            pipeline_version=pipeline_version(pipeline),
            config_hash=sha256_hex(config_hash),
            status=RunStatus.QUEUED,
            attempt=1,
            progress=None,
            error=None,
            diagnostics_ref=None,
            started_at=None,
            finished_at=None,
            stamp=AuditStamp.initial(now),
        )

    @property
    def retryable(self) -> bool:
        """A temporary failure, or a run the user cancelled themselves, can be re-queued.
        Permanent failures (e.g. UNSUPPORTED_FORMAT) are never offered for retry."""
        if self.status is RunStatus.CANCELLED:
            return True
        return self.status is RunStatus.FAILED and self.error is not None and self.error.retryable

    def advance(
        self, target: RunStatus, *, now: datetime, progress: RunProgress | None = None
    ) -> ProcessingRun:
        if target in {RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.RETRY_WAIT}:
            raise InvalidTransitionError("processing_run", self.status, target)
        if not run_transition_allowed(self.status, target):
            raise InvalidTransitionError("processing_run", self.status, target)
        now = ensure_utc(now)
        return replace(
            self,
            status=target,
            progress=progress if progress is not None else self.progress,
            started_at=self.started_at or (now if target is RunStatus.VALIDATING else None),
            finished_at=now if target in TERMINAL_RUN_STATES else None,
            stamp=self.stamp.bumped(now),
        )

    def fail(self, *, code: str, retryable: bool, now: datetime) -> ProcessingRun:
        if not run_transition_allowed(self.status, RunStatus.FAILED):
            raise InvalidTransitionError("processing_run", self.status, RunStatus.FAILED)
        now = ensure_utc(now)
        return replace(
            self,
            status=RunStatus.FAILED,
            error=RunError(code=error_code(code), retryable=retryable),
            finished_at=now,
            stamp=self.stamp.bumped(now),
        )

    def cancel(self, *, now: datetime) -> ProcessingRun:
        if not run_transition_allowed(self.status, RunStatus.CANCELLED):
            raise InvalidTransitionError("processing_run", self.status, RunStatus.CANCELLED)
        now = ensure_utc(now)
        return replace(
            self,
            status=RunStatus.CANCELLED,
            error=RunError(code="CANCELLED", retryable=False),
            finished_at=now,
            stamp=self.stamp.bumped(now),
        )

    def retry(self, *, now: datetime) -> ProcessingRun:
        """Manual retry of a retryable failure or a cancelled run: re-queues the same run
        (attempt + 1)."""
        if not self.retryable:
            raise InvalidTransitionError("processing_run", self.status, RunStatus.QUEUED)
        now = ensure_utc(now)
        return replace(
            self,
            status=RunStatus.QUEUED,
            attempt=self.attempt + 1,
            error=None,
            progress=None,
            finished_at=None,
            stamp=self.stamp.bumped(now),
        )
