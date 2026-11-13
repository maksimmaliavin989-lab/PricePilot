from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from pricepilot.api.deps import Services, services, set_etag
from pricepilot.api.schemas import (
    DocumentOut,
    DocumentPage,
    ImportErrorOut,
    ImportOut,
    LineOut,
    LinePageOut,
    ProcessingRunOut,
    decode_cursor,
    decode_ordinal_cursor,
    encode_cursor,
    encode_ordinal_cursor,
)
from pricepilot.application.errors import ImportRejectedError
from pricepilot.application.ports import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    DocumentFilter,
    LinePageRequest,
    PageRequest,
)
from pricepilot.domain.documents import DocumentStatus, ImportSource
from pricepilot.infrastructure.storage.file_store import LocalOriginalStore, UploadTooLargeError

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


@router.post(
    "",
    response_model=ImportOut,
    status_code=status.HTTP_201_CREATED,
    summary="Импортировать файл (тело запроса — байты файла)",
    responses={200: {"model": ImportOut, "description": "Дубликат: файл уже импортирован"}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload_document(
    request: Request,
    response: Response,
    svc: ServicesDep,
    workspace_id: UUID,
    x_file_name: Annotated[
        str,
        Header(
            max_length=1024,
            description="Имя файла в percent-encoding UTF-8 (например, encodeURIComponent)",
        ),
    ],
    supplier_id: UUID | None = None,
    source: ImportSource = ImportSource.FILE_PICKER,
) -> ImportOut:
    filename = unquote(x_file_name, errors="strict").strip()
    store: LocalOriginalStore = request.app.state.store
    with store.begin_upload() as writer:
        try:
            async for chunk in request.stream():
                writer.write(chunk)
        except UploadTooLargeError as exc:
            raise ImportRejectedError(
                "LIMIT_EXCEEDED",
                "Файл больше допустимого размера и не сохранён.",
                {"max_bytes": store.max_bytes},
            ) from exc
        incoming = writer.finish()
    if incoming.size == 0:
        store.discard(incoming)
        raise ImportRejectedError("INVALID_CONTENT", "Файл пустой и не сохранён.")
    try:
        result = await run_in_threadpool(
            svc.imports.import_file,
            workspace_id=workspace_id,
            supplier_id=supplier_id,
            filename=filename,
            incoming=incoming,
            source=source,
        )
    finally:
        store.discard(incoming)  # no-op once committed (the file has been moved)
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    run_out = (
        ProcessingRunOut.of(await run_in_threadpool(svc.processing.get, result.run.id))
        if result.run is not None
        else None
    )
    document = await run_in_threadpool(svc.documents.get, result.document.id)
    return ImportOut(
        document=DocumentOut.of(document, run_out.id if run_out else None)
        if not result.duplicate
        else DocumentOut.of(document, svc.documents.latest_run_id(document.id)),
        run=run_out,
        duplicate_of=result.document.id if result.duplicate else None,
        error=ImportErrorOut(code=result.problem.code, message=result.problem.message)
        if result.problem
        else None,
    )


@router.post(
    "/{document_id}/processing-runs",
    response_model=ProcessingRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Запустить обработку текущей версией pipeline (идемпотентно)",
    responses={200: {"model": ProcessingRunOut, "description": "Такой запуск уже есть"}},
)
def start_processing(document_id: UUID, svc: ServicesDep, response: Response) -> ProcessingRunOut:
    run, created = svc.imports.reprocess(document_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return ProcessingRunOut.of(svc.processing.get(run.id))
