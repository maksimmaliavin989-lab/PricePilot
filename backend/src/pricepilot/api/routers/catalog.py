from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from pricepilot.api.deps import Services, services, set_etag
from pricepilot.api.schemas import (
    ProductCreate,
    ProductOut,
    ProductPage,
    UnitOut,
    decode_cursor,
    encode_cursor,
)
from pricepilot.application.catalog import IdentifierInput
from pricepilot.application.ports import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageRequest
from pricepilot.domain.values import UNITS

router = APIRouter(tags=["catalog"])
ServicesDep = Annotated[Services, Depends(services)]


@router.get("/products", response_model=ProductPage, summary="Товары каталога")
def list_products(
    svc: ServicesDep,
    workspace_id: UUID,
    query: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ProductPage:
    page = svc.catalog.list(
        workspace_id, PageRequest(limit=limit, after=decode_cursor(cursor)), query=query
    )
    return ProductPage(
        items=[ProductOut.of(v) for v in page.items], next_cursor=encode_cursor(page.next_cursor)
    )


@router.post(
    "/products",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать товар",
)
def create_product(body: ProductCreate, svc: ServicesDep, response: Response) -> ProductOut:
    view = svc.catalog.create(
        workspace_id=body.workspace_id,
        canonical_name=body.canonical_name,
        base_unit_code=body.base_unit_code,
        brand=body.brand,
        manufacturer_code=body.manufacturer_code,
        attributes=body.attributes,
        identifiers=[IdentifierInput(i.scheme, i.value, i.issuer) for i in body.identifiers],
    )
    set_etag(response, view.product.stamp.version)
    return ProductOut.of(view)


@router.get("/products/{product_id}", response_model=ProductOut, summary="Товар")
def get_product(product_id: UUID, svc: ServicesDep, response: Response) -> ProductOut:
    view = svc.catalog.get(product_id)
    set_etag(response, view.product.stamp.version)
    return ProductOut.of(view)


@router.get("/units", response_model=list[UnitOut], summary="Словарь единиц измерения")
def list_units() -> list[UnitOut]:
    return [
        UnitOut(
            code=u.code,
            dimension=u.dimension.value,
            ucum=u.ucum,
            label_ru=u.label_ru,
            label_en=u.label_en,
        )
        for u in UNITS.values()
    ]
