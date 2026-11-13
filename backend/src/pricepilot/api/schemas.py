"""Transport DTOs (API v1). Validation of business rules happens in the domain layer."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pricepilot.application.catalog import ProductView
from pricepilot.application.documents import RunView
from pricepilot.application.errors import InvalidCursorError
from pricepilot.application.ports import LineWithFields, PageCursor
from pricepilot.domain.catalog import IdentifierScheme
from pricepilot.domain.common import ensure_utc
from pricepilot.domain.documents import DocumentStatus, RunStatus, SourceDocument
from pricepilot.domain.extraction import (
    ExtractedField,
    FieldRevision,
    LineStatus,
    ReviewDecision,
    ReviewStatus,
    ValueType,
)
from pricepilot.domain.suppliers import Supplier
from pricepilot.domain.workspaces import Workspace


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Name = Annotated[str, Field(min_length=1, max_length=200)]
Locale = Annotated[str, Field(min_length=2, max_length=35, examples=["ru-RU"])]
Currency = Annotated[str, Field(min_length=3, max_length=3, examples=["RUB"])]
Country = Annotated[str, Field(min_length=2, max_length=2, examples=["RU"])]
TaxId = Annotated[str, Field(max_length=64)]
Terms = Annotated[str, Field(max_length=4000)]


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    correlation_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthOut(BaseModel):
    status: str
    version: str
    api_version: str
    storage_mode: str
    database: str


class WorkspaceCreate(_Strict):
    name: Name
    locale: Locale
    base_currency: Currency
    country_code: Country


class WorkspacePatchIn(_Strict):
    name: Name | None = None
    locale: Locale | None = None
    base_currency: Currency | None = None
    country_code: Country | None = None


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    locale: str
    base_currency: str
    country_code: str
    storage_mode: str
    version: int
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def of(ws: Workspace) -> WorkspaceOut:
        return WorkspaceOut(
            id=ws.id,
            name=ws.name,
            locale=ws.locale,
            base_currency=ws.base_currency,
            country_code=ws.country_code,
            storage_mode=ws.storage_mode.value,
            version=ws.stamp.version,
            created_at=ws.stamp.created_at,
            updated_at=ws.stamp.updated_at,
        )


class WorkspacePage(BaseModel):
    items: list[WorkspaceOut]
    next_cursor: str | None


class SupplierCreate(_Strict):
    workspace_id: UUID
    name: Name
    country_code: Country
    default_currency: Currency
    tax_id: TaxId | None = None
    terms: Terms | None = None


class SupplierPatchIn(_Strict):
    name: Name | None = None
    country_code: Country | None = None
    default_currency: Currency | None = None
    tax_id: TaxId | None = Field(default=None, description="null очищает значение")
    terms: Terms | None = Field(default=None, description="null очищает значение")
    archived: bool | None = Field(
        default=None, description="true архивирует, false восстанавливает"
    )


class SupplierOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    country_code: str
    default_currency: str
    tax_id: str | None
    terms: str | None
    archived_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def of(s: Supplier) -> SupplierOut:
        return SupplierOut(
            id=s.id,
            workspace_id=s.workspace_id,
            name=s.name,
            country_code=s.country_code,
            default_currency=s.default_currency,
            tax_id=s.tax_id,
            terms=s.terms,
            archived_at=s.archived_at,
            version=s.stamp.version,
            created_at=s.stamp.created_at,
            updated_at=s.stamp.updated_at,
        )


class SupplierPage(BaseModel):
    items: list[SupplierOut]
    next_cursor: str | None


def encode_cursor(cursor: PageCursor | None) -> str | None:
    if cursor is None:
        return None
    raw = json.dumps({"c": cursor.created_at.isoformat(), "i": str(cursor.id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str | None) -> PageCursor | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return PageCursor(
            created_at=ensure_utc(datetime.fromisoformat(data["c"])), id=UUID(data["i"])
        )
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise InvalidCursorError from exc


def encode_ordinal_cursor(ordinal: int | None) -> str | None:
    if ordinal is None:
        return None
    return base64.urlsafe_b64encode(json.dumps({"o": ordinal}).encode()).decode().rstrip("=")


def decode_ordinal_cursor(value: str | None) -> int | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        ordinal = json.loads(base64.urlsafe_b64decode(padded.encode()))["o"]
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise InvalidCursorError from exc
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
        raise InvalidCursorError
    return ordinal


# --- Documents & processing runs -------------------------------------------------------


class DocumentOut(BaseModel):
    id: UUID
    workspace_id: UUID
    supplier_id: UUID | None
    original_name: str
    detected_mime: str
    extension: str
    size_bytes: int
    sha256: str
    import_source: str
    status: DocumentStatus
    retention_state: str
    latest_run_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def of(d: SourceDocument, latest_run_id: UUID | None) -> DocumentOut:
        return DocumentOut(
            id=d.id,
            workspace_id=d.workspace_id,
            supplier_id=d.supplier_id,
            original_name=d.original_name,
            detected_mime=d.detected_mime,
            extension=d.extension,
            size_bytes=d.size_bytes,
            sha256=d.sha256,
            import_source=d.import_source.value,
            status=d.status,
            retention_state=d.retention_state.value,
            latest_run_id=latest_run_id,
            version=d.stamp.version,
            created_at=d.stamp.created_at,
            updated_at=d.stamp.updated_at,
        )


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    next_cursor: str | None


class RunProgressOut(BaseModel):
    stage: str
    completed: int
    total: int | None


class RunErrorOut(BaseModel):
    code: str
    retryable: bool


class RunCountsOut(BaseModel):
    lines: int
    fields: int
    lines_review_required: int


class ProcessingRunOut(BaseModel):
    id: UUID
    document_id: UUID
    pipeline_version: str
    config_hash: str
    status: RunStatus
    attempt: int
    progress: RunProgressOut | None
    error: RunErrorOut | None
    counts: RunCountsOut
    started_at: datetime | None
    finished_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def of(view: RunView) -> ProcessingRunOut:
        r = view.run
        return ProcessingRunOut(
            id=r.id,
            document_id=r.document_id,
            pipeline_version=r.pipeline_version,
            config_hash=r.config_hash,
            status=r.status,
            attempt=r.attempt,
            progress=(
                RunProgressOut(
                    stage=r.progress.stage, completed=r.progress.completed, total=r.progress.total
                )
                if r.progress
                else None
            ),
            error=RunErrorOut(code=r.error.code, retryable=r.error.retryable) if r.error else None,
            counts=RunCountsOut(
                lines=view.counts.lines,
                fields=view.counts.fields,
                lines_review_required=view.counts.lines_review_required,
            ),
            started_at=r.started_at,
            finished_at=r.finished_at,
            version=r.stamp.version,
            created_at=r.stamp.created_at,
            updated_at=r.stamp.updated_at,
        )


# --- Extraction ------------------------------------------------------------------------


class RegionOut(BaseModel):
    x: float
    y: float
    width: float
    height: float


class SourceLocationOut(BaseModel):
    sheet: str | None
    page: int | None
    row: int | None
    column: int | None
    cell: str | None
    region: RegionOut | None
    localization: Literal["exact", "approximate", "none"]


class ProvenanceOut(SourceLocationOut):
    source_document_id: UUID
    extractor: str
    extractor_version: str
    pipeline_version: str
    method: str
    header: str | None = None
    formula: str | None = None


class FieldOut(BaseModel):
    id: UUID
    line_id: UUID
    field_key: str
    value_type: ValueType
    raw_value: str | None
    normalized_value: dict[str, str] | None = Field(
        description="Каноническое значение; числа — десятичные строки, например "
        '{"amount": "1234.50", "currency": "RUB"}'
    )
    confidence: float | None
    provenance: ProvenanceOut
    review_status: ReviewStatus
    revision: int
    reviewed_by: str | None
    reviewed_at: datetime | None

    @staticmethod
    def of(f: ExtractedField) -> FieldOut:
        return FieldOut(
            id=f.id,
            line_id=f.line_id,
            field_key=f.field_key,
            value_type=f.value_type,
            raw_value=f.raw_value,
            normalized_value=f.normalized_value,
            confidence=f.confidence,
            provenance=ProvenanceOut.model_validate(f.provenance),
            review_status=f.review_status,
            revision=f.revision,
            reviewed_by=f.reviewed_by,
            reviewed_at=f.reviewed_at,
        )


class LineOut(BaseModel):
    id: UUID
    run_id: UUID
    ordinal: int
    raw_text: str
    source: SourceLocationOut
    status: LineStatus
    review_reasons: list[str] = Field(
        description="Почему строку должен проверить человек: MULTIPLE_PRICE_COLUMNS, "
        "AMBIGUOUS_NUMBER, INVALID_NUMBER, MISSING_PRICE, MISSING_UNIT, UNKNOWN_UNIT, "
        "ASSUMED_CURRENCY"
    )
    fields: list[FieldOut]

    @staticmethod
    def of(item: LineWithFields) -> LineOut:
        line = item.line
        return LineOut(
            id=line.id,
            run_id=line.run_id,
            ordinal=line.ordinal,
            raw_text=line.raw_text,
            source=SourceLocationOut.model_validate(line.location.to_json()),
            status=line.status,
            review_reasons=list(line.review_reasons),
            fields=[FieldOut.of(f) for f in item.fields],
        )


class LinePageOut(BaseModel):
    run_id: UUID | None
    items: list[LineOut]
    next_cursor: str | None


class FieldCorrectionIn(_Strict):
    value: dict[str, Any] = Field(
        description="Новое нормализованное значение по value_type поля: text/decimal/integer/"
        "unit/currency/document_date → {value}; money → {amount, currency}; quantity → "
        "{value, unit}. Числа передаются строками."
    )
    reason: Annotated[str, Field(max_length=1000)] | None = None
    expected_revision: Annotated[int, Field(ge=1)]


class FieldDecisionIn(_Strict):
    decision: ReviewDecision
    reason: Annotated[str, Field(max_length=1000)] | None = None
    expected_revision: Annotated[int, Field(ge=1)]


class FieldRevisionOut(BaseModel):
    revision: int
    action: str
    previous_value: dict[str, str] | None
    new_value: dict[str, str] | None
    previous_status: ReviewStatus
    new_status: ReviewStatus
    reason: str | None
    actor: str
    occurred_at: datetime

    @staticmethod
    def of(r: FieldRevision) -> FieldRevisionOut:
        return FieldRevisionOut(
            revision=r.revision,
            action=r.action,
            previous_value=r.previous_value,
            new_value=r.new_value,
            previous_status=r.previous_status,
            new_status=r.new_status,
            reason=r.reason,
            actor=r.actor,
            occurred_at=r.occurred_at,
        )


# --- Catalog ---------------------------------------------------------------------------


class IdentifierIn(_Strict):
    scheme: IdentifierScheme
    value: Annotated[str, Field(min_length=1, max_length=256)]
    issuer: Annotated[str, Field(max_length=200)] | None = None


class ProductCreate(_Strict):
    workspace_id: UUID
    canonical_name: Annotated[str, Field(min_length=1, max_length=300)]
    base_unit_code: Annotated[str, Field(min_length=1, max_length=16, examples=["pc"])]
    brand: Annotated[str, Field(max_length=200)] | None = None
    manufacturer_code: Annotated[str, Field(max_length=128)] | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    identifiers: list[IdentifierIn] = Field(default_factory=list, max_length=50)


class IdentifierOut(BaseModel):
    scheme: IdentifierScheme
    value: str
    normalized_value: str
    issuer: str | None


class ProductOut(BaseModel):
    id: UUID
    workspace_id: UUID
    canonical_name: str
    brand: str | None
    manufacturer_code: str | None
    attributes: dict[str, str]
    base_unit_code: str
    active: bool
    identifiers: list[IdentifierOut]
    version: int
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def of(view: ProductView) -> ProductOut:
        p = view.product
        return ProductOut(
            id=p.id,
            workspace_id=p.workspace_id,
            canonical_name=p.canonical_name,
            brand=p.brand,
            manufacturer_code=p.manufacturer_code,
            attributes=p.attributes,
            base_unit_code=p.base_unit_code,
            active=p.active,
            identifiers=[
                IdentifierOut(
                    scheme=i.scheme,
                    value=i.value,
                    normalized_value=i.normalized_value,
                    issuer=i.issuer,
                )
                for i in view.identifiers
            ],
            version=p.stamp.version,
            created_at=p.stamp.created_at,
            updated_at=p.stamp.updated_at,
        )


class ProductPage(BaseModel):
    items: list[ProductOut]
    next_cursor: str | None


class UnitOut(BaseModel):
    code: str
    dimension: str
    ucum: str
    label_ru: str
    label_en: str


class ImportErrorOut(BaseModel):
    code: str
    message: str


class ImportOut(BaseModel):
    document: DocumentOut
    run: ProcessingRunOut | None
    duplicate_of: UUID | None = Field(
        description="Тот же файл (SHA-256) уже импортирован в это рабочее пространство"
    )
    error: ImportErrorOut | None = Field(
        description="Файл сохранён, но не будет/не может быть обработан: код и объяснение"
    )
