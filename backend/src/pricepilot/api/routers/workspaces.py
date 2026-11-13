from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status

from pricepilot.api.deps import Services, expected_version, services, set_etag
from pricepilot.api.schemas import (
    WorkspaceCreate,
    WorkspaceOut,
    WorkspacePage,
    WorkspacePatchIn,
    decode_cursor,
    encode_cursor,
)
from pricepilot.application.ports import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageRequest
from pricepilot.application.services import WorkspacePatch

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
ServicesDep = Annotated[Services, Depends(services)]


@router.get("", response_model=WorkspacePage, summary="Список рабочих пространств")
def list_workspaces(
    svc: ServicesDep,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> WorkspacePage:
    page = svc.workspaces.list(PageRequest(limit=limit, after=decode_cursor(cursor)))
    return WorkspacePage(
        items=[WorkspaceOut.of(w) for w in page.items], next_cursor=encode_cursor(page.next_cursor)
    )


@router.post(
    "",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать рабочее пространство",
)
def create_workspace(body: WorkspaceCreate, svc: ServicesDep, response: Response) -> WorkspaceOut:
    workspace = svc.workspaces.create(
        name=body.name,
        locale=body.locale,
        base_currency=body.base_currency,
        country_code=body.country_code,
    )
    set_etag(response, workspace.stamp.version)
    return WorkspaceOut.of(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceOut, summary="Рабочее пространство")
def get_workspace(workspace_id: UUID, svc: ServicesDep, response: Response) -> WorkspaceOut:
    workspace = svc.workspaces.get(workspace_id)
    set_etag(response, workspace.stamp.version)
    return WorkspaceOut.of(workspace)


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceOut,
    summary="Изменить рабочее пространство (If-Match: версия)",
)
def patch_workspace(
    workspace_id: UUID,
    body: WorkspacePatchIn,
    svc: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> WorkspaceOut:
    version = expected_version(if_match)
    workspace = svc.workspaces.update(
        workspace_id,
        WorkspacePatch(
            name=body.name,
            locale=body.locale,
            base_currency=body.base_currency,
            country_code=body.country_code,
        ),
        version,
    )
    set_etag(response, workspace.stamp.version)
    return WorkspaceOut.of(workspace)
