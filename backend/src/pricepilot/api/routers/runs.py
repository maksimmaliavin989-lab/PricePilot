from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from pricepilot.api.deps import Services, services, set_etag
from pricepilot.api.schemas import ProcessingRunOut

router = APIRouter(prefix="/processing-runs", tags=["processing"])
ServicesDep = Annotated[Services, Depends(services)]


def _out(svc: Services, run_id: UUID, response: Response) -> ProcessingRunOut:
    view = svc.processing.get(run_id)
    set_etag(response, view.run.stamp.version)
    return ProcessingRunOut.of(view)


@router.get("/{run_id}", response_model=ProcessingRunOut, summary="Статус и прогресс запуска")
def get_run(run_id: UUID, svc: ServicesDep, response: Response) -> ProcessingRunOut:
    return _out(svc, run_id, response)


@router.post(
    "/{run_id}/cancel", response_model=ProcessingRunOut, summary="Отменить незавершённый запуск"
)
def cancel_run(run_id: UUID, svc: ServicesDep, response: Response) -> ProcessingRunOut:
    svc.processing.cancel(run_id)
    return _out(svc, run_id, response)


@router.post(
    "/{run_id}/retry",
    response_model=ProcessingRunOut,
    summary="Повторить запуск (только для ошибок с retryable=true)",
)
def retry_run(run_id: UUID, svc: ServicesDep, response: Response) -> ProcessingRunOut:
    svc.processing.retry(run_id)
    return _out(svc, run_id, response)
