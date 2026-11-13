from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status

from pricepilot.api.deps import Services, expected_version, services, set_etag
from pricepilot.api.schemas import (
    SupplierCreate,
    SupplierOut,
    SupplierPage,
    SupplierPatchIn,
    decode_cursor,
    encode_cursor,
)
from pricepilot.application.ports import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageRequest
from pricepilot.application.services import SupplierPatch

router = APIRouter(prefix="/suppliers", tags=["suppliers"])
ServicesDep = Annotated[Services, Depends(services)]


@router.get("", response_model=SupplierPage, summary="Поставщики рабочего пространства")
def list_suppliers(
    svc: ServicesDep,
    workspace_id: UUID,
    query: Annotated[str | None, Query(max_length=200)] = None,
    include_archived: bool = False,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> SupplierPage:
    page = svc.suppliers.list(
        workspace_id,
        PageRequest(limit=limit, after=decode_cursor(cursor)),
        query=query,
        include_archived=include_archived,
    )
    return SupplierPage(
        items=[SupplierOut.of(s) for s in page.items], next_cursor=encode_cursor(page.next_cursor)
    )


@router.post(
    "",
    response_model=SupplierOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать поставщика",
)
def create_supplier(body: SupplierCreate, svc: ServicesDep, response: Response) -> SupplierOut:
    supplier = svc.suppliers.create(
        workspace_id=body.workspace_id,
        name=body.name,
        country_code=body.country_code,
        default_currency=body.default_currency,
        tax_id=body.tax_id,
        terms=body.terms,
    )
    set_etag(response, supplier.stamp.version)
    return SupplierOut.of(supplier)


@router.get("/{supplier_id}", response_model=SupplierOut, summary="Поставщик")
def get_supplier(supplier_id: UUID, svc: ServicesDep, response: Response) -> SupplierOut:
    supplier = svc.suppliers.get(supplier_id)
    set_etag(response, supplier.stamp.version)
    return SupplierOut.of(supplier)


@router.patch(
    "/{supplier_id}",
    response_model=SupplierOut,
    summary="Изменить или архивировать поставщика (If-Match: версия)",
)
def patch_supplier(
    supplier_id: UUID,
    body: SupplierPatchIn,
    svc: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> SupplierOut:
    version = expected_version(if_match)
    supplier = svc.suppliers.update(
        supplier_id,
        SupplierPatch(
            name=body.name,
            country_code=body.country_code,
            default_currency=body.default_currency,
            tax_id=body.tax_id,
            terms=body.terms,
            archived=body.archived,
            provided=frozenset(body.model_fields_set),
        ),
        version,
    )
    set_etag(response, supplier.stamp.version)
    return SupplierOut.of(supplier)
