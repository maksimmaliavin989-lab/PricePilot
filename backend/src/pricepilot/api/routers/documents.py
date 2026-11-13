from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from pricepilot.api.deps import Services, services, set_etag
from pricepilot.api.schemas import (
    DocumentOut,
    DocumentPage,
    LineOut,
    LinePageOut,
    decode_cursor,
    decode_ordinal_cursor,
    encode_cursor,
    encode_ordinal_cursor,
)
from pricepilot.application.ports import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    DocumentFilter,
    LinePageRequest,
    PageRequest,
)
from pricepilot.domain.documents import DocumentStatus

router = APIRouter(prefix="/documents", tags=["documents"])
ServicesDep = Annotated[Services, Depends(services)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
Cursor = Annotated[str | None, Query(max_length=512)]


@router.get("", response_model=DocumentPage, summary="Документы рабочего пространства")
def list_documents(
    svc: ServicesDep,
    workspace_id: UUID,
    status: DocumentStatus | None = None,
    supplier_id: UUID | None = None,
    cursor: Cursor = None,
    limit: Limit = DEFAULT_PAGE_SIZE,
) -> DocumentPage:
    page = svc.documents.list(
        workspace_id,
        PageRequest(limit=limit, after=decode_cursor(cursor)),
        DocumentFilter(status=status, supplier_id=supplier_id),
    )
    return DocumentPage(
        items=[DocumentOut.of(d, svc.documents.latest_run_id(d.id)) for d in page.items],
        next_cursor=encode_cursor(page.next_cursor),
    )


@router.get("/{document_id}", response_model=DocumentOut, summary="Метаданные и статус документа")
def get_document(document_id: UUID, svc: ServicesDep, response: Response) -> DocumentOut:
    document = svc.documents.get(document_id)
    set_etag(response, document.stamp.version)
    return DocumentOut.of(document, svc.documents.latest_run_id(document_id))


@router.get(
    "/{document_id}/lines",
    response_model=LinePageOut,
    summary="Извлечённые строки с полями и происхождением (по умолчанию — последний запуск)",
)
def list_lines(
    document_id: UUID,
    svc: ServicesDep,
    run_id: UUID | None = None,
    cursor: Cursor = None,
    limit: Limit = DEFAULT_PAGE_SIZE,
) -> LinePageOut:
    effective_run = run_id or svc.documents.latest_run_id(document_id)
    page = svc.processing.lines(
        document_id,
        run_id,
        LinePageRequest(limit=limit, after_ordinal=decode_ordinal_cursor(cursor)),
    )
    return LinePageOut(
        run_id=effective_run,
        items=[LineOut.of(item) for item in page.items],
        next_cursor=encode_ordinal_cursor(page.next_ordinal),
    )
