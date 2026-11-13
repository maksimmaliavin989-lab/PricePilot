from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from pricepilot.api.deps import Services, services
from pricepilot.api.schemas import FieldCorrectionIn, FieldDecisionIn, FieldOut, FieldRevisionOut

router = APIRouter(prefix="/extracted-fields", tags=["review"])
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
