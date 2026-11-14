"""Transport DTOs for offers, matching and landed cost (API v1, TASK_006).

Decimal values travel as canonical strings ("1500.00"); the domain validates them.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pricepilot.application.costs import (
    ComparisonOfferView,
    ComparisonView,
    ItemView,
    PolicyView,
    RateSourceView,
)
from pricepilot.application.offers import OfferState
from pricepilot.domain.common import DomainValidationError
from pricepilot.domain.costs import (
    Basis,
    Category,
    ComponentType,
    CostComponent,
    FxRate,
    InputStatus,
    PercentBase,
)
from pricepilot.domain.matching import Candidate, DecisionKind, MatchDecision
from pricepilot.domain.offers import Comparison, OfferTerms, SkippedLine, SupplierOffer
from pricepilot.domain.values import decimal_text, parse_decimal

DecimalStr = Annotated[
    str, Field(min_length=1, max_length=40, pattern=r"^-?\d+(\.\d+)?$", examples=["1500.00"])
]
Currency = Annotated[str, Field(min_length=3, max_length=3, examples=["RUB"])]
Text200 = Annotated[str, Field(min_length=1, max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MoneyOut(BaseModel):
    amount: str
    currency: str


class QuantityIO(_Strict):
    value: DecimalStr
    unit: Annotated[str, Field(min_length=1, max_length=16, examples=["kg"])]


# --- offers -----------------------------------------------------------------------------------


class PackOut(BaseModel):
    unit: str
    content: QuantityIO
    source: str = Field(description="stated | name (подсказка из названия) | user")
    evidence: str
    confirmed: bool


class MatchDecisionOut(BaseModel):
    id: UUID
    product_id: UUID
    decision: DecisionKind
    actor: str
    reason: str | None
    supersedes_id: UUID | None
    override: bool = Field(description="Подтверждено вопреки конфликту, найденному сопоставителем")
    decided_at: datetime

    @staticmethod
    def of(d: MatchDecision) -> MatchDecisionOut:
        return MatchDecisionOut(
            id=d.id,
            product_id=d.product_id,
            decision=d.kind,
            actor=d.actor,
            reason=d.reason,
            supersedes_id=d.supersedes_id,
            override=d.override,
            decided_at=d.decided_at,
        )


class OfferOut(BaseModel):
    id: UUID
    workspace_id: UUID
    supplier_id: UUID | None
    source_document_id: UUID
    extracted_line_id: UUID
    line_ordinal: int = Field(description="Номер строки в прайсе")
    price_field_key: str
    name: str
    supplier_sku: str | None
    unit: str | None
    price: MoneyOut
    price_confirmed: bool
    pack: PackOut | None
    matched_product_id: UUID | None
    decisions: list[MatchDecisionOut]
    version: int

    @staticmethod
    def of(state: OfferState) -> OfferOut:
        o: SupplierOffer = state.offer
        pack = o.pack
        return OfferOut(
            id=o.id,
            workspace_id=o.workspace_id,
            supplier_id=o.supplier_id,
            source_document_id=o.document_id,
            extracted_line_id=o.line_id,
            line_ordinal=o.line_ordinal,
            price_field_key=o.price_field_key,
            name=o.name,
            supplier_sku=o.supplier_sku,
            unit=o.unit,
            price=MoneyOut(amount=decimal_text(o.price.amount), currency=o.price.currency),
            price_confirmed=o.price_confirmed,
            pack=PackOut(
                unit=pack.unit,
                content=QuantityIO(value=decimal_text(pack.content.value), unit=pack.content.unit),
                source=pack.source.value,
                evidence=pack.evidence,
                confirmed=pack.confirmed,
            )
            if pack
            else None,
            matched_product_id=state.history.accepted_product,
            decisions=[MatchDecisionOut.of(d) for d in state.history.decisions],
            version=o.stamp.version,
        )


class OfferPage(BaseModel):
    items: list[OfferOut]
    next_cursor: str | None


class SkippedLineOut(BaseModel):
    line_id: UUID
    ordinal: int
    reason: str

    @staticmethod
    def of(s: SkippedLine) -> SkippedLineOut:
        return SkippedLineOut(line_id=s.line_id, ordinal=s.ordinal, reason=s.reason)


class OfferImportOut(BaseModel):
    created: int
    existing: int = Field(description="Уже были и не изменились")
    updated: int = Field(description="Уже были и обновлены по проверенной строке")
    skipped: list[SkippedLineOut]


class PackIn(_Strict):
    content: QuantityIO = Field(description="Сколько базовых единиц в одной единице цены")


class FeatureOut(BaseModel):
    code: str
    weight: str
    detail: str
    blocking: bool


class CandidateOut(BaseModel):
    product_id: UUID
    canonical_name: str
    base_unit_code: str
    score: str
    blocked: bool
    features: list[FeatureOut]
    matcher_version: str


class CandidatesOut(BaseModel):
    offer: OfferOut
    candidates: list[CandidateOut]
    auto_link_enabled: bool = Field(
        description="Автосвязывание выключено до проверки порога на реальных обезличенных прайсах"
    )


def candidate_out(c: Candidate, name: str, unit: str) -> CandidateOut:
    return CandidateOut(
        product_id=c.product_id,
        canonical_name=name,
        base_unit_code=unit,
        score=decimal_text(c.score),
        blocked=c.blocked,
        features=[
            FeatureOut(
                code=f.code.value,
                weight=decimal_text(f.weight),
                detail=f.detail,
                blocking=f.blocking,
            )
            for f in c.features
        ],
        matcher_version=c.matcher_version,
    )


class MatchDecisionIn(_Strict):
    decision: DecisionKind
    product_id: UUID
    reason: Annotated[str, Field(max_length=1000)] | None = None


class ProductFromOfferIn(_Strict):
    canonical_name: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    base_unit_code: Annotated[str, Field(min_length=1, max_length=16)] | None = None


# --- cost policy ------------------------------------------------------------------------------


class ComponentTypeIO(_Strict):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$", examples=["unloading"])]
    label: Annotated[str, Field(min_length=1, max_length=100)]
    category: Category
    basis: Basis
    base: PercentBase = PercentBase.GOODS_NET
    required: bool = False
    default_value: DecimalStr | None = Field(
        default=None, description="Только для сборов и скидок; у налогов и пошлин не бывает"
    )
    default_currency: Currency | None = None

    def to_domain(self) -> ComponentType:
        return ComponentType.create(
            code=self.code,
            label=self.label,
            category=self.category,
            basis=self.basis,
            base=self.base,
            required=self.required,
            default_value=parse_decimal(self.default_value, "default_value")
            if self.default_value
            else None,
            default_currency=self.default_currency,
        )

    @staticmethod
    def of(t: ComponentType) -> ComponentTypeIO:
        return ComponentTypeIO(
            code=t.code,
            label=t.label,
            category=t.category,
            basis=t.basis,
            base=t.base,
            required=t.required,
            default_value=decimal_text(t.default_value) if t.default_value is not None else None,
            default_currency=t.default_currency,
        )


class CostPolicyOut(BaseModel):
    name: str
    types: list[ComponentTypeIO]
    fingerprint: str
    version: int = Field(description="0 — встроенная политика, ещё не сохранена")

    @staticmethod
    def of(view: PolicyView) -> CostPolicyOut:
        return CostPolicyOut(
            name=view.policy.name,
            types=[ComponentTypeIO.of(t) for t in view.policy.types],
            fingerprint=view.policy.fingerprint,
            version=view.version,
        )


class CostPolicyIn(_Strict):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    types: list[ComponentTypeIO] = Field(max_length=50)
    expected_version: Annotated[int, Field(ge=0)]


# --- FX and verified tax rates ---------------------------------------------------------------


class FxRateIn(_Strict):
    base: Currency
    quote: Currency
    rate: DecimalStr = Field(description="1 base = rate × quote")
    effective_date: date
    source: Text200


class FxRateOut(BaseModel):
    id: UUID
    base: str
    quote: str
    rate: str
    effective_date: date
    source: str

    @staticmethod
    def of(rate_id: UUID, r: FxRate) -> FxRateOut:
        return FxRateOut(
            id=rate_id,
            base=r.base,
            quote=r.quote,
            rate=decimal_text(r.rate),
            effective_date=r.effective_date,
            source=r.source,
        )


class TaxRateIO(_Strict):
    component_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
    rate: DecimalStr = Field(description="Процент")
    valid_from: date
    valid_to: date | None = None
    scope: Annotated[str, Field(max_length=200)] | None = None


class TaxRateSourceIn(_Strict):
    name: Text200
    reference: Annotated[str, Field(min_length=1, max_length=500)]
    verified_by: Text200
    verified_at: datetime
    rates: list[TaxRateIO] = Field(min_length=1, max_length=200)


class TaxRateSourceOut(BaseModel):
    id: UUID
    name: str
    reference: str
    verified_by: str
    verified_at: datetime
    rates: list[TaxRateIO]

    @staticmethod
    def of(view: RateSourceView) -> TaxRateSourceOut:
        s = view.source
        return TaxRateSourceOut(
            id=s.id,
            name=s.name,
            reference=s.reference,
            verified_by=s.verified_by,
            verified_at=s.verified_at,
            rates=[
                TaxRateIO(
                    component_code=r.component_code,
                    rate=decimal_text(r.rate),
                    valid_from=r.valid_from,
                    valid_to=r.valid_to,
                    scope=r.scope,
                )
                for r in view.rates
            ],
        )


# --- comparisons ------------------------------------------------------------------------------


class ComparisonCreate(_Strict):
    workspace_id: UUID
    name: Text200
    target_currency: Currency
    as_of: date = Field(description="Дата курсов валют и ставок")


class ComparisonItemIn(_Strict):
    product_id: UUID
    required_quantity: QuantityIO = Field(description="В базовой единице товара")


class ComponentIO(_Strict):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$", examples=["delivery"])]
    status: InputStatus = InputStatus.KNOWN
    value: DecimalStr | None = None
    currency: Currency | None = None
    basis: Basis | None = None
    base: PercentBase | None = None
    source: Annotated[str, Field(max_length=500)] | None = Field(
        default=None, description="Откуда значение; для налогов и пошлин обязательно"
    )

    def to_domain(self) -> CostComponent:
        if self.status is InputStatus.UNKNOWN:
            return CostComponent.unknown(self.code, self.source)
        if self.status is InputStatus.NOT_APPLICABLE:
            return CostComponent.not_applicable(self.code, self.source)
        if self.value is None:
            raise DomainValidationError("components.value", f"«{self.code}»: нет значения.")
        return CostComponent.known(
            self.code,
            parse_decimal(self.value, "components.value"),
            currency=self.currency,
            source=self.source,
            basis=self.basis,
            base=self.base,
        )

    @staticmethod
    def of(c: CostComponent) -> ComponentIO:
        return ComponentIO(
            code=c.code,
            status=c.status,
            value=decimal_text(c.value) if c.value is not None else None,
            currency=c.currency,
            basis=c.basis,
            base=c.base,
            source=c.source,
        )


class OfferTermsIO(_Strict):
    components: list[ComponentIO] = Field(default_factory=list, max_length=50)
    minimum_order: DecimalStr | None = Field(default=None, description="В единицах цены")
    order_multiple: DecimalStr | None = Field(default=None, description="В единицах цены")
    price_includes_vat: bool | None = Field(
        default=None, description="null — неизвестно; налог тогда не добавляется"
    )

    def to_domain(self) -> OfferTerms:
        return OfferTerms(
            components=tuple(c.to_domain() for c in self.components),
            minimum_order=parse_decimal(self.minimum_order, "minimum_order")
            if self.minimum_order
            else None,
            order_multiple=parse_decimal(self.order_multiple, "order_multiple")
            if self.order_multiple
            else None,
            price_includes_vat=self.price_includes_vat,
        )

    @staticmethod
    def of(t: OfferTerms) -> OfferTermsIO:
        return OfferTermsIO(
            components=[ComponentIO.of(c) for c in t.components],
            minimum_order=decimal_text(t.minimum_order) if t.minimum_order else None,
            order_multiple=decimal_text(t.order_multiple) if t.order_multiple else None,
            price_includes_vat=t.price_includes_vat,
        )


class ComparisonOfferIn(OfferTermsIO):
    offer_id: UUID


class CostWarningOut(BaseModel):
    code: str
    message: str


class CostComponentOut(BaseModel):
    code: str
    label: str
    category: str
    status: str
    amount: str | None = Field(description="null — неизвестно (не ноль)")
    currency: str
    detail: str
    origin: str | None
    original: MoneyOut | None
    fx: dict[str, Any] | None
    source: str | None
    verified_rate: dict[str, Any] | None


class CostSnapshotOut(BaseModel):
    id: UUID
    created_at: datetime
    formula_version: str
    policy_fingerprint: str
    input_hash: str
    ordered: QuantityIO
    purchased: QuantityIO
    components: list[CostComponentOut]
    total: MoneyOut | None = Field(description="null, пока есть неизвестные компоненты")
    unit_cost: MoneyOut | None = Field(description="За базовую единицу товара")
    known_subtotal: MoneyOut = Field(description="Сумма только известных частей — не итог")
    unknown_components: list[str]
    warnings: list[CostWarningOut]
    complete: bool
    supersedes_id: UUID | None

    @staticmethod
    def of(data: dict[str, Any]) -> CostSnapshotOut:
        return CostSnapshotOut.model_validate({k: v for k, v in data.items() if k != "input"})


class CostSnapshotDetailOut(CostSnapshotOut):
    input: dict[str, Any] = Field(description="Копия всех входных данных расчёта")

    @staticmethod
    def of(data: dict[str, Any]) -> CostSnapshotDetailOut:
        return CostSnapshotDetailOut.model_validate(data)


class ComparisonOfferOut(BaseModel):
    id: UUID
    offer: OfferOut
    match_decision_id: UUID
    terms: OfferTermsIO
    snapshot: CostSnapshotOut | None

    @staticmethod
    def of(v: ComparisonOfferView, offer: OfferOut) -> ComparisonOfferOut:
        return ComparisonOfferOut(
            id=v.comparison_offer.id,
            offer=offer,
            match_decision_id=v.comparison_offer.match_decision_id,
            terms=OfferTermsIO.of(v.comparison_offer.terms),
            snapshot=CostSnapshotOut.of(v.snapshot) if v.snapshot else None,
        )


class ComparisonItemOut(BaseModel):
    id: UUID
    product_id: UUID
    product_name: str
    base_unit_code: str
    required_quantity: QuantityIO
    offers: list[ComparisonOfferOut]


class ComparisonOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    target_currency: str
    as_of: date
    created_at: datetime
    items: list[ComparisonItemOut]


class ComparisonSummaryOut(BaseModel):
    id: UUID
    name: str
    target_currency: str
    as_of: date
    created_at: datetime

    @staticmethod
    def of(c: Comparison) -> ComparisonSummaryOut:
        return ComparisonSummaryOut(
            id=c.id,
            name=c.name,
            target_currency=c.target_currency,
            as_of=c.as_of,
            created_at=c.stamp.created_at,
        )


class ComparisonPage(BaseModel):
    items: list[ComparisonSummaryOut]
    next_cursor: str | None


def item_out(v: ItemView, offers: list[ComparisonOfferOut]) -> ComparisonItemOut:
    return ComparisonItemOut(
        id=v.item.id,
        product_id=v.product.id,
        product_name=v.product.canonical_name,
        base_unit_code=v.product.base_unit_code,
        required_quantity=QuantityIO(
            value=decimal_text(v.item.required.value), unit=v.item.required.unit
        ),
        offers=offers,
    )


def comparison_out(view: ComparisonView, offer_out: Any) -> ComparisonOut:
    c = view.comparison
    return ComparisonOut(
        id=c.id,
        workspace_id=c.workspace_id,
        name=c.name,
        target_currency=c.target_currency,
        as_of=c.as_of,
        created_at=c.stamp.created_at,
        items=[
            item_out(i, [ComparisonOfferOut.of(o, offer_out(o)) for o in i.offers])
            for i in view.items
        ],
    )
