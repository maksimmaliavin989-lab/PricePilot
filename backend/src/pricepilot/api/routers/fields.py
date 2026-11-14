from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from pricepilot.api.deps import Services, services
from pricepilot.api.schemas import (
    FieldAddIn,
    FieldCorrectionIn,
    FieldDecisionIn,
    FieldOut,
    FieldRevisionOut,
    LineOut,
)

router = APIRouter(prefix="/extracted-fields", tags=["review"])
lines_router = APIRouter(prefix="/extracted-lines", tags=["review"])
ServicesDep = Annotated[Services, Depends(services)]


@router.get("/{field_id}", response_model=FieldOut, summary="Извлечённое поле")
def get_field(field_id: UUID, svc: ServicesDep) -> FieldOut:
    return FieldOut.of(svc.review.get(field_id))


@router.patch(
    "/{field_id}",
    response_model=FieldOut,
    summary="Исправить нормализованное значение (raw_value не меняется; создаётся ревизия)",
)
def correct_field(field_id: UUID, body: FieldCorrectionIn, svc: ServicesDep) -> FieldOut:
    return FieldOut.of(
        svc.review.correct(
            field_id, value=body.value, reason=body.reason, expected_revision=body.expected_revision
        )
    )


@router.post(
    "/{field_id}/decision", response_model=FieldOut, summary="Подтвердить или отклонить поле"
)
def decide_field(field_id: UUID, body: FieldDecisionIn, svc: ServicesDep) -> FieldOut:
    return FieldOut.of(
        svc.review.decide(
            field_id,
            decision=body.decision,
            reason=body.reason,
            expected_revision=body.expected_revision,
        )
    )


@router.get(
    "/{field_id}/revisions", response_model=list[FieldRevisionOut], summary="История правок поля"
)
def field_revisions(field_id: UUID, svc: ServicesDep) -> list[FieldRevisionOut]:
    return [FieldRevisionOut.of(r) for r in svc.review.revisions(field_id)]


@lines_router.get("/{line_id}", response_model=LineOut, summary="Строка с полями и замечаниями")
def get_line(line_id: UUID, svc: ServicesDep) -> LineOut:
    return LineOut.of(svc.review.line(line_id))


@lines_router.post(
    "/{line_id}/fields",
    response_model=LineOut,
    status_code=201,
    summary="Добавить недостающее поле (единицу или цену)",
    description="Для строк, где в файле нет единицы или цены. raw_value остаётся пустым, "
    "происхождение — method=user; создаётся ревизия. Если поле уже есть — 422.",
)
def add_field(line_id: UUID, body: FieldAddIn, svc: ServicesDep) -> LineOut:
    svc.review.add_field(line_id, key=body.field_key, value=body.value, reason=body.reason)
    return LineOut.of(svc.review.line(line_id))
