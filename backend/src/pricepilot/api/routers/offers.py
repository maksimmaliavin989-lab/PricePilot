"""Supplier offers, match candidates and decisions (TASK_006)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status

from pricepilot.api.cost_schemas import (
    CandidatesOut,
    MatchDecisionIn,
    OfferImportOut,
    OfferOut,
    OfferPage,
    PackIn,
    ProductFromOfferIn,
    SkippedLineOut,
    candidate_out,
)
from pricepilot.api.deps import Services, expected_version, services, set_etag
from pricepilot.api.schemas import ProductOut, decode_cursor, encode_cursor
from pricepilot.application.ports import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageRequest
from pricepilot.domain.matching import AutoLinkPolicy
from pricepilot.domain.values import Quantity

router = APIRouter(tags=["offers"])
ServicesDep = Annotated[Services, Depends(services)]


@router.post(
    "/documents/{document_id}/offers",
    response_model=OfferImportOut,
    summary="Создать предложения из проверенных строк документа",
    description="Идемпотентно: существующие предложения не дублируются, а обновляются по "
    "проверенной строке (подтверждённая или исправленная цена). Строки без цены, "
    "с отклонённым названием или с несколькими ценами без выбора пропускаются с причиной.",
)
def create_offers(document_id: UUID, svc: ServicesDep) -> OfferImportOut:
    result = svc.offers.create_from_document(document_id)
    return OfferImportOut(
        created=len(result.created),
        existing=result.existing,
        updated=result.updated,
        skipped=[SkippedLineOut.of(s) for s in result.skipped],
    )


@router.get("/offers", response_model=OfferPage, summary="Предложения поставщиков")
def list_offers(
    svc: ServicesDep,
    workspace_id: UUID,
    document_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> OfferPage:
    page = svc.offers.list(
        workspace_id, PageRequest(limit=limit, after=decode_cursor(cursor)), document_id=document_id
    )
    return OfferPage(
        items=[OfferOut.of(s) for s in page.items], next_cursor=encode_cursor(page.next_cursor)
    )


@router.get("/offers/{offer_id}", response_model=OfferOut, summary="Предложение")
def get_offer(offer_id: UUID, svc: ServicesDep, response: Response) -> OfferOut:
    state = svc.offers.get(offer_id)
    set_etag(response, state.offer.stamp.version)
    return OfferOut.of(state)


@router.put(
    "/offers/{offer_id}/pack",
    response_model=OfferOut,
    summary="Подтвердить или указать фасовку",
    description="Сколько базовых единиц в одной единице цены (например, 50 kg в мешке). "
    "Требует If-Match с версией предложения.",
)
def set_pack(
    offer_id: UUID,
    body: PackIn,
    svc: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> OfferOut:
    state = svc.offers.set_pack(
        offer_id,
        Quantity.parse(body.content.model_dump(), "content"),
        expected_version(if_match),
    )
    set_etag(response, state.offer.stamp.version)
    return OfferOut.of(state)


@router.get(
    "/offers/{offer_id}/candidates",
    response_model=CandidatesOut,
    summary="Кандидаты на сопоставление с причинами",
)
def offer_candidates(
    offer_id: UUID, svc: ServicesDep, limit: Annotated[int, Query(ge=1, le=20)] = 5
) -> CandidatesOut:
    state, found = svc.offers.candidates(offer_id, limit)
    names = {}
    for c in found:
        view = svc.catalog.get(c.product_id)
        names[c.product_id] = (view.product.canonical_name, view.product.base_unit_code)
    return CandidatesOut(
        offer=OfferOut.of(state),
        candidates=[candidate_out(c, *names[c.product_id]) for c in found],
        auto_link_enabled=AutoLinkPolicy().enabled,
    )


@router.post(
    "/offers/{offer_id}/match-decisions",
    response_model=OfferOut,
    status_code=status.HTTP_201_CREATED,
    summary="Подтвердить, отклонить или отменить сопоставление",
    description="Решения только добавляются; отмена создаёт решение revoke со ссылкой на "
    "прежнее. Подтверждение кандидата с конфликтом требует причины.",
)
def decide(offer_id: UUID, body: MatchDecisionIn, svc: ServicesDep) -> OfferOut:
    state = svc.offers.decide(
        offer_id, kind=body.decision, product_id=body.product_id, reason=body.reason
    )
    return OfferOut.of(state)


@router.post(
    "/offers/{offer_id}/product",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать товар каталога из предложения и сопоставить",
)
def product_from_offer(offer_id: UUID, body: ProductFromOfferIn, svc: ServicesDep) -> ProductOut:
    _, product = svc.offers.create_product(
        offer_id, canonical_name=body.canonical_name, base_unit_code=body.base_unit_code
    )
    return ProductOut.of(svc.catalog.get(product.id))
