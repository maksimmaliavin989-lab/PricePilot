"""Cost policy, exchange and tax rates, comparisons and landed-cost snapshots (TASK_006)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pricepilot.api.cost_schemas import (
    ComparisonCreate,
    ComparisonItemIn,
    ComparisonOfferIn,
    ComparisonOut,
    ComparisonPage,
    ComparisonSummaryOut,
    CostPolicyIn,
    CostPolicyOut,
    CostSnapshotDetailOut,
    FxRateIn,
    FxRateOut,
    OfferOut,
    OfferTermsIO,
    TaxRateSourceIn,
    TaxRateSourceOut,
    comparison_out,
)
from pricepilot.api.deps import Services, services
from pricepilot.api.schemas import decode_cursor, encode_cursor
from pricepilot.application.costs import ComparisonOfferView, ComparisonView
from pricepilot.application.ports import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageRequest
from pricepilot.domain.costs import FxRate
from pricepilot.domain.values import Quantity, parse_decimal

router = APIRouter(tags=["costs"])
ServicesDep = Annotated[Services, Depends(services)]


def _out(svc: Services, view: ComparisonView) -> ComparisonOut:
    def offer(v: ComparisonOfferView) -> OfferOut:
        return OfferOut.of(svc.offers.get(v.offer.id))

    return comparison_out(view, offer)


@router.get(
    "/workspaces/{workspace_id}/cost-policy",
    response_model=CostPolicyOut,
    summary="Политика стоимости (компоненты и их порядок)",
)
def get_policy(workspace_id: UUID, svc: ServicesDep) -> CostPolicyOut:
    return CostPolicyOut.of(svc.costs.policy(workspace_id))


@router.put(
    "/workspaces/{workspace_id}/cost-policy",
    response_model=CostPolicyOut,
    summary="Изменить политику стоимости",
    description="Типы компонентов в порядке расчёта; свои типы добавляются сюда. Налоги и "
    "пошлины не имеют значений по умолчанию.",
)
def put_policy(workspace_id: UUID, body: CostPolicyIn, svc: ServicesDep) -> CostPolicyOut:
    view = svc.costs.save_policy(
        workspace_id,
        name=body.name,
        types=[t.to_domain() for t in body.types],
        expected_version=body.expected_version,
    )
    return CostPolicyOut.of(view)


@router.get(
    "/workspaces/{workspace_id}/fx-rates", response_model=list[FxRateOut], summary="Курсы валют"
)
def list_fx(workspace_id: UUID, svc: ServicesDep) -> list[FxRateOut]:
    return [FxRateOut.of(i, r) for i, r in svc.costs.list_fx(workspace_id)]


@router.post(
    "/workspaces/{workspace_id}/fx-rates",
    response_model=FxRateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить курс валюты (дата и источник обязательны)",
)
def add_fx(workspace_id: UUID, body: FxRateIn, svc: ServicesDep) -> FxRateOut:
    rate = FxRate.create(
        base=body.base,
        quote=body.quote,
        rate=parse_decimal(body.rate, "rate"),
        effective_date=body.effective_date,
        source=body.source,
    )
    return FxRateOut.of(*svc.costs.add_fx(workspace_id, rate))


@router.get(
    "/workspaces/{workspace_id}/tax-rate-sources",
    response_model=list[TaxRateSourceOut],
    summary="Проверенные источники ставок налогов и пошлин",
)
def list_rate_sources(workspace_id: UUID, svc: ServicesDep) -> list[TaxRateSourceOut]:
    return [TaxRateSourceOut.of(v) for v in svc.costs.rate_sources(workspace_id)]


@router.post(
    "/workspaces/{workspace_id}/tax-rate-sources",
    response_model=TaxRateSourceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить проверенный источник ставок",
    description="Документ, кто и когда проверил, ставки со сроком действия. Ставка применяется "
    "автоматически только в срок действия; иначе налог в расчёте неизвестен.",
)
def add_rate_source(
    workspace_id: UUID, body: TaxRateSourceIn, svc: ServicesDep
) -> TaxRateSourceOut:
    view = svc.costs.add_rate_source(
        workspace_id,
        name=body.name,
        reference=body.reference,
        verified_by=body.verified_by,
        verified_at=body.verified_at,
        rates=[
            (r.component_code, parse_decimal(r.rate, "rate"), r.valid_from, r.valid_to, r.scope)
            for r in body.rates
        ],
    )
    return TaxRateSourceOut.of(view)


@router.post(
    "/comparisons",
    response_model=ComparisonOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать сравнение",
)
def create_comparison(body: ComparisonCreate, svc: ServicesDep) -> ComparisonOut:
    view = svc.costs.create_comparison(
        body.workspace_id, name=body.name, target_currency=body.target_currency, as_of=body.as_of
    )
    return _out(svc, view)


@router.get("/comparisons", response_model=ComparisonPage, summary="Сравнения")
def list_comparisons(
    svc: ServicesDep,
    workspace_id: UUID,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ComparisonPage:
    page = svc.costs.list_comparisons(
        workspace_id, PageRequest(limit=limit, after=decode_cursor(cursor))
    )
    return ComparisonPage(
        items=[ComparisonSummaryOut.of(c) for c in page.items],
        next_cursor=encode_cursor(page.next_cursor),
    )


@router.get("/comparisons/{comparison_id}", response_model=ComparisonOut, summary="Сравнение")
def get_comparison(comparison_id: UUID, svc: ServicesDep) -> ComparisonOut:
    return _out(svc, svc.costs.get(comparison_id))


@router.post(
    "/comparisons/{comparison_id}/items",
    response_model=ComparisonOut,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить товар и требуемое количество",
)
def add_item(comparison_id: UUID, body: ComparisonItemIn, svc: ServicesDep) -> ComparisonOut:
    view = svc.costs.add_item(
        comparison_id,
        product_id=body.product_id,
        required=Quantity.parse(body.required_quantity.model_dump(), "required_quantity"),
    )
    return _out(svc, view)


@router.post(
    "/comparison-items/{item_id}/offers",
    response_model=ComparisonOut,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить сопоставленное предложение с условиями",
)
def add_offer(item_id: UUID, body: ComparisonOfferIn, svc: ServicesDep) -> ComparisonOut:
    terms = OfferTermsIO.model_validate(body.model_dump(exclude={"offer_id"})).to_domain()
    return _out(svc, svc.costs.add_offer(item_id, offer_id=body.offer_id, terms=terms))


@router.put(
    "/comparison-offers/{comparison_offer_id}/terms",
    response_model=ComparisonOut,
    summary="Изменить условия предложения в сравнении",
)
def update_terms(comparison_offer_id: UUID, body: OfferTermsIO, svc: ServicesDep) -> ComparisonOut:
    return _out(svc, svc.costs.update_terms(comparison_offer_id, body.to_domain()))


@router.post(
    "/comparisons/{comparison_id}/calculate",
    response_model=ComparisonOut,
    summary="Рассчитать полную стоимость (новые неизменяемые снимки)",
)
def calculate(comparison_id: UUID, svc: ServicesDep) -> ComparisonOut:
    return _out(svc, svc.costs.calculate(comparison_id))


@router.get(
    "/cost-snapshots/{snapshot_id}",
    response_model=CostSnapshotDetailOut,
    summary="Снимок расчёта со всеми входными данными",
)
def get_snapshot(snapshot_id: UUID, svc: ServicesDep) -> CostSnapshotDetailOut:
    return CostSnapshotDetailOut.of(svc.costs.snapshot(snapshot_id))
