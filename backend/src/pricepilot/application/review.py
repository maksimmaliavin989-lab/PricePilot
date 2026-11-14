"""Use cases: user review of extracted fields (correct / accept / reject) with history."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pricepilot.application.errors import NotFoundError, RevisionConflictError
from pricepilot.application.ports import (
    Clock,
    CorrelationProvider,
    LineWithFields,
    UnitOfWorkFactory,
)
from pricepilot.domain.audit import LOCAL_ACTOR, AuditEvent
from pricepilot.domain.common import DomainValidationError, utc_now
from pricepilot.domain.extraction import (
    ExtractedField,
    FieldRevision,
    Provenance,
    ReviewDecision,
    StaleRevisionError,
    ValueType,
)


def _no_correlation() -> str | None:
    return None


class ReviewService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock = utc_now,
        correlation: CorrelationProvider = _no_correlation,
        actor: str = LOCAL_ACTOR,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._correlation = correlation
        self._actor = actor

    def add_field(
        self, line_id: UUID, *, key: str, value: Any, reason: str | None
    ) -> ExtractedField:
        """Add a field the source line does not have (unit or price), entered by the user."""
        value_types = {"unit": ValueType.UNIT, "price": ValueType.MONEY}
        if key not in value_types:
            raise DomainValidationError("field_key", "Добавить можно только единицу или цену.")
        with self._uow() as uow:
            item = uow.extraction.get_line(line_id)
            context = uow.extraction.line_context(line_id)
            if item is None or context is None:
                raise NotFoundError("extracted_line")
            document_id, workspace_id, pipeline = context
            existing = {f.field_key for f in item.fields}
            if key in existing or (
                key == "price" and any(k.startswith("price_") for k in existing)
            ):
                raise DomainValidationError(
                    "field_key", "Поле уже есть в строке: исправьте или подтвердите его."
                )
            now = self._clock()
            field, revision = ExtractedField.user_entered(
                line_id=line_id,
                key=key,
                value_type=value_types[key],
                value=value,
                provenance=Provenance(
                    source_document_id=document_id,
                    location=item.line.location,
                    extractor="user",
                    extractor_version="1",
                    pipeline_version=pipeline,
                    method="user",
                ),
                reason=reason,
                actor=self._actor,
                now=now,
            )
            uow.extraction.add_field(field)
            uow.extraction.add_revision(revision)
            uow.audit.add(
                AuditEvent.record(
                    workspace_id=workspace_id,
                    actor_id=self._actor,
                    entity_type="extracted_field",
                    entity_id=field.id,
                    action="add",
                    before=None,
                    after={"key": key, "value": field.normalized_value},
                    now=now,
                    correlation_id=self._correlation(),
                )
            )
            uow.commit()
        return field

    def line(self, line_id: UUID) -> LineWithFields:
        with self._uow() as uow:
            item = uow.extraction.get_line(line_id)
        if item is None:
            raise NotFoundError("extracted_line")
        return item

    def get(self, field_id: UUID) -> ExtractedField:
        with self._uow() as uow:
            field = uow.extraction.get_field(field_id)
        if field is None:
            raise NotFoundError("extracted_field")
        return field

    def revisions(self, field_id: UUID) -> list[FieldRevision]:
        with self._uow() as uow:
            if uow.extraction.get_field(field_id) is None:
                raise NotFoundError("extracted_field")
            return uow.extraction.list_revisions(field_id)

    def correct(
        self, field_id: UUID, *, value: Any, reason: str | None, expected_revision: int
    ) -> ExtractedField:
        return self._apply(field_id, "correct", expected_revision, reason, value=value)

    def decide(
        self,
        field_id: UUID,
        *,
        decision: ReviewDecision,
        reason: str | None,
        expected_revision: int,
    ) -> ExtractedField:
        return self._apply(field_id, decision.value, expected_revision, reason, decision=decision)

    def _apply(
        self,
        field_id: UUID,
        action: str,
        expected_revision: int,
        reason: str | None,
        *,
        value: Any = None,
        decision: ReviewDecision | None = None,
    ) -> ExtractedField:
        with self._uow() as uow:
            field = uow.extraction.get_field(field_id)
            workspace_id = uow.extraction.field_workspace(field_id)
            if field is None or workspace_id is None:
                raise NotFoundError("extracted_field")
            now = self._clock()
            try:
                if decision is None:
                    updated, revision = field.corrected(
                        value=value,
                        reason=reason,
                        actor=self._actor,
                        expected_revision=expected_revision,
                        now=now,
                    )
                else:
                    updated, revision = field.decided(
                        decision=decision,
                        reason=reason,
                        actor=self._actor,
                        expected_revision=expected_revision,
                        now=now,
                    )
            except StaleRevisionError as exc:
                raise RevisionConflictError("extracted_field", exc.current) from exc
            if not uow.extraction.update_field(updated, expected_revision):
                latest = uow.extraction.get_field(field_id)
                raise RevisionConflictError("extracted_field", latest.revision if latest else 0)
            uow.extraction.add_revision(revision)
            uow.audit.add(
                AuditEvent.record(
                    workspace_id=workspace_id,
                    actor_id=self._actor,
                    entity_type="extracted_field",
                    entity_id=field_id,
                    action=action,
                    before={"value": field.normalized_value, "status": field.review_status.value},
                    after={
                        "value": updated.normalized_value,
                        "status": updated.review_status.value,
                    },
                    now=now,
                    correlation_id=self._correlation(),
                )
            )
            uow.commit()
        return updated
