"""Canonical extraction output: lines, fields, provenance and review revisions.

Invariants (DATA_MODELS.md, ADR-006):
* ``raw_value`` is never overwritten; corrections change ``normalized_value`` only;
* every correction or review decision is a new revision (additive history);
* provenance always says where a value came from and how precisely it is localised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pricepilot.domain.common import (
    DomainValidationError,
    currency_code,
    ensure_utc,
    new_id,
    optional_text,
)
from pricepilot.domain.values import Money, Quantity, decimal_text, parse_decimal, unit_code

_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_CELL_RE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
MAX_RAW_TEXT = 10_000
MAX_REASON = 1_000


class ValueType(StrEnum):
    TEXT = "text"
    DECIMAL = "decimal"
    INTEGER = "integer"
    MONEY = "money"
    QUANTITY = "quantity"
    UNIT = "unit"
    CURRENCY = "currency"
    DOCUMENT_DATE = "document_date"  # calendar date printed in a document, not a moment


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class LineStatus(StrEnum):
    PENDING = "pending"
    REVIEW_REQUIRED = "review_required"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Localization(StrEnum):
    EXACT = "exact"  # cell / bounding box
    APPROXIMATE = "approximate"  # line or row only
    NONE = "none"  # nearest available source only


class ReviewDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


def field_key(value: str) -> str:
    if not _FIELD_KEY_RE.fullmatch(value):
        raise DomainValidationError("field_key", "Ключ поля: латиница в нижнем регистре и _.")
    return value


def normalize_value(value_type: ValueType, payload: Any, field: str = "value") -> dict[str, Any]:
    """Validate a normalized value and return its canonical JSON (decimals as strings)."""
    if value_type is ValueType.MONEY:
        return Money.parse(payload, field).to_json()
    if value_type is ValueType.QUANTITY:
        return Quantity.parse(payload, field).to_json()
    if not isinstance(payload, dict) or set(payload) != {"value"}:
        raise DomainValidationError(field, "Ожидается объект {value}.")
    raw = payload["value"]
    if value_type is ValueType.TEXT:
        text = optional_text(raw if isinstance(raw, str) else None, field, MAX_RAW_TEXT)
        if text is None:
            raise DomainValidationError(field, "Текст не может быть пустым.")
        return {"value": text}
    if value_type is ValueType.DECIMAL:
        return {"value": decimal_text(parse_decimal(raw, field, allow_negative=True))}
    if value_type is ValueType.INTEGER:
        if not isinstance(raw, str) or not re.fullmatch(r"-?\d{1,18}", raw):
            raise DomainValidationError(field, "Целое число передаётся строкой.")
        return {"value": str(int(raw))}
    if value_type is ValueType.UNIT:
        return {"value": unit_code(raw, field)}
    if value_type is ValueType.CURRENCY:
        return {"value": currency_code(str(raw), field)}
    if value_type is ValueType.DOCUMENT_DATE:
        try:
            return {"value": date.fromisoformat(str(raw)).isoformat()}
        except ValueError as exc:
            raise DomainValidationError(field, "Дата в формате YYYY-MM-DD.") from exc
    raise DomainValidationError(field, "Неизвестный тип значения.")  # pragma: no cover


@dataclass(frozen=True, slots=True)
class Region:
    """Bounding box normalised to the page: 0..1 from the top-left corner."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if (
            any(not 0.0 <= v <= 1.0 for v in values)
            or self.x + self.width > 1.0 + 1e-9
            or (self.y + self.height > 1.0 + 1e-9)
        ):
            raise DomainValidationError("region", "Координаты области должны быть в 0..1.")

    def to_json(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Where in the original a line or field sits. Rows/pages are 1-based."""

    sheet: str | None = None
    page: int | None = None
    row: int | None = None
    column: int | None = None
    cell: str | None = None
    region: Region | None = None

    def __post_init__(self) -> None:
        for name in ("page", "row", "column"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise DomainValidationError(name, "Нумерация источника начинается с 1.")
        if self.cell is not None and not _CELL_RE.fullmatch(self.cell):
            raise DomainValidationError("cell", "Адрес ячейки в формате A1.")
        if self.sheet is not None and optional_text(self.sheet, "sheet", 255) is None:
            raise DomainValidationError("sheet", "Пустое имя листа.")

    @property
    def localization(self) -> Localization:
        if self.cell is not None or self.region is not None:
            return Localization.EXACT
        if self.row is not None or self.page is not None:
            return Localization.APPROXIMATE
        return Localization.NONE

    def to_json(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "page": self.page,
            "row": self.row,
            "column": self.column,
            "cell": self.cell,
            "region": self.region.to_json() if self.region else None,
            "localization": self.localization.value,
        }


@dataclass(frozen=True, slots=True)
class Provenance:
    source_document_id: UUID
    location: SourceLocation
    extractor: str
    extractor_version: str
    pipeline_version: str
    method: str  # e.g. "spreadsheet_cell", "pdf_text", "ocr", "user"
    header: str | None = None  # source column heading, e.g. "Опт, руб."
    formula: str | None = None  # spreadsheet formula text (never evaluated)

    def to_json(self) -> dict[str, Any]:
        return {
            "source_document_id": str(self.source_document_id),
            **self.location.to_json(),
            "extractor": self.extractor,
            "extractor_version": self.extractor_version,
            "pipeline_version": self.pipeline_version,
            "method": self.method,
            "header": self.header,
            "formula": self.formula,
        }


@dataclass(frozen=True, slots=True)
class ExtractedField:
    id: UUID
    line_id: UUID
    field_key: str
    value_type: ValueType
    raw_value: str | None
    normalized_value: dict[str, Any] | None
    confidence: float | None
    provenance: dict[str, Any]
    review_status: ReviewStatus
    revision: int
    reviewed_by: str | None
    reviewed_at: datetime | None

    @staticmethod
    def extracted(
        *,
        line_id: UUID,
        key: str,
        value_type: ValueType,
        raw_value: str | None,
        normalized: Any | None,
        confidence: float | None,
        provenance: Provenance,
    ) -> ExtractedField:
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise DomainValidationError("confidence", "Уверенность должна быть в 0..1.")
        if raw_value is not None and len(raw_value) > MAX_RAW_TEXT:
            raise DomainValidationError("raw_value", "Слишком длинное исходное значение.")
        return ExtractedField(
            id=new_id(),
            line_id=line_id,
            field_key=field_key(key),
            value_type=value_type,
            raw_value=raw_value,
            normalized_value=(
                normalize_value(value_type, normalized) if normalized is not None else None
            ),
            confidence=confidence,
            provenance=provenance.to_json(),
            review_status=ReviewStatus.UNREVIEWED,
            revision=1,
            reviewed_by=None,
            reviewed_at=None,
        )

    def _check_revision(self, expected_revision: int) -> None:
        if expected_revision != self.revision:
            raise StaleRevisionError(self.revision)

    def corrected(
        self, *, value: Any, reason: str | None, actor: str, expected_revision: int, now: datetime
    ) -> tuple[ExtractedField, FieldRevision]:
        self._check_revision(expected_revision)
        normalized = normalize_value(self.value_type, value)
        updated = replace(
            self,
            normalized_value=normalized,
            review_status=ReviewStatus.CORRECTED,
            revision=self.revision + 1,
            reviewed_by=actor,
            reviewed_at=ensure_utc(now),
        )
        return updated, FieldRevision.of(self, updated, "correct", reason, now)

    def decided(
        self,
        *,
        decision: ReviewDecision,
        reason: str | None,
        actor: str,
        expected_revision: int,
        now: datetime,
    ) -> tuple[ExtractedField, FieldRevision]:
        self._check_revision(expected_revision)
        if decision is ReviewDecision.ACCEPT and self.normalized_value is None:
            raise DomainValidationError(
                "decision", "Нельзя подтвердить поле без нормализованного значения: исправьте его."
            )
        accept = decision is ReviewDecision.ACCEPT
        status = ReviewStatus.ACCEPTED if accept else ReviewStatus.REJECTED
        updated = replace(
            self,
            review_status=status,
            revision=self.revision + 1,
            reviewed_by=actor,
            reviewed_at=ensure_utc(now),
        )
        return updated, FieldRevision.of(self, updated, decision.value, reason, now)


class StaleRevisionError(Exception):
    def __init__(self, current: int) -> None:
        super().__init__("stale revision")
        self.current = current


@dataclass(frozen=True, slots=True)
class FieldRevision:
    id: UUID
    field_id: UUID
    revision: int
    action: str  # correct | accept | reject
    previous_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    previous_status: ReviewStatus
    new_status: ReviewStatus
    reason: str | None
    actor: str
    occurred_at: datetime

    @staticmethod
    def of(
        before: ExtractedField,
        after: ExtractedField,
        action: str,
        reason: str | None,
        now: datetime,
    ) -> FieldRevision:
        return FieldRevision(
            id=new_id(),
            field_id=after.id,
            revision=after.revision,
            action=action,
            previous_value=before.normalized_value,
            new_value=after.normalized_value,
            previous_status=before.review_status,
            new_status=after.review_status,
            reason=optional_text(reason, "reason", MAX_REASON),
            actor=after.reviewed_by or "",
            occurred_at=ensure_utc(now),
        )


@dataclass(frozen=True, slots=True)
class ExtractedLine:
    id: UUID
    run_id: UUID
    ordinal: int
    raw_text: str
    location: SourceLocation
    status: LineStatus
    review_reasons: tuple[str, ...] = ()

    @staticmethod
    def extracted(
        *,
        run_id: UUID,
        ordinal: int,
        raw_text: str,
        location: SourceLocation,
        review_reasons: tuple[str, ...] = (),
    ) -> ExtractedLine:
        for reason in review_reasons:
            if not _REASON_RE.fullmatch(reason):
                raise DomainValidationError("review_reasons", "Код причины: UPPER_SNAKE_CASE.")
        if ordinal < 1:
            raise DomainValidationError("ordinal", "Порядковый номер строки начинается с 1.")
        if len(raw_text) > MAX_RAW_TEXT:
            raise DomainValidationError("raw_text", "Слишком длинная строка.")
        return ExtractedLine(
            id=new_id(),
            run_id=run_id,
            ordinal=ordinal,
            raw_text=raw_text,
            location=location,
            status=LineStatus.REVIEW_REQUIRED if review_reasons else LineStatus.PENDING,
            review_reasons=tuple(dict.fromkeys(review_reasons)),
        )


@dataclass(frozen=True, slots=True)
class NewField:
    """Parser output for one field, before persistence."""

    key: str
    value_type: ValueType
    raw_value: str | None
    normalized: Any | None
    confidence: float | None
    location: SourceLocation
    method: str
    header: str | None = None
    formula: str | None = None


@dataclass(frozen=True, slots=True)
class NewLine:
    raw_text: str
    location: SourceLocation
    fields: tuple[NewField, ...]
    review_reasons: tuple[str, ...] = ()  # why a person must check this line
