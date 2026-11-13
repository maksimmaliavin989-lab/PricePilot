"""Offer → product matching: normalisation, explainable candidates, decisions (TASK_006).

A candidate is a *suggestion* with the features that produced its score; nothing is linked
until a decision is recorded (by the user, or by an explicitly configured auto-link policy).
Decisions are append-only: changing one creates a new decision that supersedes it, so the
history and the original link stay visible (FR-15).

Rules that matter for purchasing:
* the same supplier SKU at a different supplier is not evidence (a SKU is the supplier's own
  code);
* different packaging or different attributes (grade М400 vs М500, size 600×300×200 vs
  600×250×200) block a match — different products are never merged silently;
* a different pack of a product sold by weight/volume (25 vs 50 kg of a product counted in
  kg) is not a conflict: it is normalised by the cost calculation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pricepilot.domain.catalog import (
    IdentifierScheme,
    Product,
    ProductIdentifier,
    normalize_identifier,
)
from pricepilot.domain.common import DomainValidationError, ensure_utc, new_id, optional_text
from pricepilot.domain.units import ConversionError, Pack, conversion_to, dimension_of
from pricepilot.domain.values import Dimension, decimal_text

MATCHER_VERSION = "match-2026.1"
MIN_CANDIDATE_SCORE = Decimal("0.30")

# --- text normalisation -----------------------------------------------------------------------

# For comparison only (the raw name is never changed): Latin look-alikes of Cyrillic
# letters, as mixed by people and OCR ("M500"/"М500", "D500"/"Д500").
_LOOKALIKES = str.maketrans("abcekmhoptxyd", "асcекмнортхуд")
_DIMENSION = re.compile(
    r"(\d+(?:[.,]\d+)?)(?:\s*[×x*хx]\s*(\d+(?:[.,]\d+)?)){1,2}(?:\s*(?:мм|см|м)(?![а-яa-z]))?"
)
_MEASURE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(мм|см|мкм|кг|гр|г|т|мл|л|шт|м²|м2|м³|м3|м|в)(?![а-яa-z0-9²³])"
)
_GRADE = re.compile(r"(?<![а-яa-z0-9])([а-яa-z]{1,3})-?(\d{2,4})(?![0-9])")
_TOKEN = re.compile(r"[а-яa-z0-9]+")
_STOPWORDS = frozenset({"и", "в", "на", "для", "с", "по", "из", "под", "без"})
_MEASURE_UNITS = {
    "мм": ("length_mm", Decimal(1)),
    "см": ("length_mm", Decimal(10)),
    "мкм": ("length_mm", Decimal("0.001")),
    "м": ("length_mm", Decimal(1000)),
    "кг": ("mass_kg", Decimal(1)),
    "г": ("mass_kg", Decimal("0.001")),
    "гр": ("mass_kg", Decimal("0.001")),
    "т": ("mass_kg", Decimal(1000)),
    "л": ("volume_l", Decimal(1)),
    "мл": ("volume_l", Decimal("0.001")),
    "шт": ("count", Decimal(1)),
    "м2": ("area_m2", Decimal(1)),
    "м²": ("area_m2", Decimal(1)),
    "м3": ("volume_l", Decimal(1000)),
    "м³": ("volume_l", Decimal(1000)),
    "в": ("voltage", Decimal(1)),
}


def fold(text: str) -> str:
    """Comparison key: NFKC, case-fold, ё→е, Latin look-alikes→Cyrillic, ``о``→``0`` inside
    numbers ("М50о" read by OCR), unified multiplication sign."""
    value = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    value = value.translate(_LOOKALIKES).replace("×", "x").replace("х", "x")
    value = re.sub(r"(?<=\d)о|о(?=\d)", "0", value)
    return re.sub(r"\s+", " ", value).strip()


def _num(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


@dataclass(frozen=True, slots=True)
class NameFeatures:
    """What a name says, in comparable form."""

    tokens: frozenset[str]
    dimensions: frozenset[tuple[Decimal, ...]]  # 600×300×200
    measures: frozenset[tuple[str, Decimal]]  # ("mass_kg", 50), ("length_mm", 12.5)
    grades: frozenset[str]  # "м500", "д500", "а500"

    @staticmethod
    def of(name: str) -> NameFeatures:
        text = fold(name)
        dimensions = set()
        for m in _DIMENSION.finditer(text):
            dimensions.add(tuple(_num(n) for n in re.findall(r"\d+(?:[.,]\d+)?", m.group(0))))
        rest = _DIMENSION.sub(" ", text)
        measures = set()
        for m in _MEASURE.finditer(rest):
            kind, factor = _MEASURE_UNITS[m.group(2)]
            measures.add((kind, (_num(m.group(1)) * factor).normalize()))
        rest = _MEASURE.sub(" ", rest)
        grades = {f"{m.group(1)}{m.group(2)}" for m in _GRADE.finditer(rest)}
        rest = _GRADE.sub(" ", rest)
        tokens = {
            t
            for t in _TOKEN.findall(rest)
            if len(t) > 1 and t not in _STOPWORDS and not t.isdigit()
        }
        return NameFeatures(
            frozenset(tokens), frozenset(dimensions), frozenset(measures), frozenset(grades)
        )


def token_similarity(a: frozenset[str], b: frozenset[str]) -> Decimal:
    """Dice coefficient on tokens; tokens sharing a ≥5-letter stem count (склонения)."""
    if not a or not b:
        return Decimal(0)

    def same(x: str, y: str) -> bool:
        return x == y or (len(x) >= 5 and len(y) >= 5 and x[:5] == y[:5])

    common = sum(1 for x in a if any(same(x, y) for y in b))
    return (Decimal(2 * common) / Decimal(len(a) + len(b))).quantize(Decimal("0.01"))


# --- candidates -------------------------------------------------------------------------------


class Feature(StrEnum):
    GTIN_EQUAL = "GTIN_EQUAL"
    GTIN_CONFLICT = "GTIN_CONFLICT"
    SUPPLIER_SKU_EQUAL = "SUPPLIER_SKU_EQUAL"
    SKU_OF_OTHER_SUPPLIER = "SKU_OF_OTHER_SUPPLIER"
    MANUFACTURER_CODE_EQUAL = "MANUFACTURER_CODE_EQUAL"
    NAME_TOKENS = "NAME_TOKENS"
    ATTRIBUTE_EQUAL = "ATTRIBUTE_EQUAL"
    ATTRIBUTE_CONFLICT = "ATTRIBUTE_CONFLICT"
    PACK_CONFLICT = "PACK_CONFLICT"
    PACK_DIFFERS_NORMALIZABLE = "PACK_DIFFERS_NORMALIZABLE"
    UNIT_INCOMPATIBLE = "UNIT_INCOMPATIBLE"


@dataclass(frozen=True, slots=True)
class FeatureScore:
    code: Feature
    weight: Decimal  # contribution to the score; 0 for purely informative features
    detail: str
    blocking: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "weight": decimal_text(self.weight),
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass(frozen=True, slots=True)
class OfferView:
    """What matching needs to know about one supplier offer (an extracted line)."""

    offer_id: UUID
    supplier_id: UUID
    name: str
    unit: str | None
    supplier_sku: str | None = None
    gtin: str | None = None
    manufacturer_code: str | None = None
    pack: Pack | None = None


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    product: Product
    identifiers: tuple[ProductIdentifier, ...] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    offer_id: UUID
    product_id: UUID
    score: Decimal  # 0..1, two decimals
    features: tuple[FeatureScore, ...]
    matcher_version: str = MATCHER_VERSION

    @property
    def blocked(self) -> bool:
        return any(f.blocking for f in self.features)

    def to_json(self) -> dict[str, object]:
        return {
            "offer_id": str(self.offer_id),
            "product_id": str(self.product_id),
            "score": decimal_text(self.score),
            "blocked": self.blocked,
            "features": [f.to_json() for f in self.features],
            "matcher_version": self.matcher_version,
        }


def _ids(entry: CatalogEntry, scheme: IdentifierScheme) -> list[ProductIdentifier]:
    return [i for i in entry.identifiers if i.scheme is scheme]


def _identifier_features(offer: OfferView, entry: CatalogEntry) -> list[FeatureScore]:
    out: list[FeatureScore] = []
    if offer.gtin:
        try:
            gtin = normalize_identifier(IdentifierScheme.GTIN, offer.gtin)
        except DomainValidationError:
            gtin = None
        known = {i.normalized_value for i in _ids(entry, IdentifierScheme.GTIN)}
        if gtin and gtin in known:
            out.append(FeatureScore(Feature.GTIN_EQUAL, Decimal("0.60"), f"GTIN {gtin}"))
        elif gtin and known:
            out.append(
                FeatureScore(
                    Feature.GTIN_CONFLICT,
                    Decimal(0),
                    f"GTIN предложения {gtin} не совпадает с GTIN товара",
                    blocking=True,
                )
            )
    if offer.supplier_sku:
        sku = code_key(offer.supplier_sku)
        for ident in _ids(entry, IdentifierScheme.SUPPLIER_SKU):
            if code_key(ident.value) != sku:
                continue
            if ident.issuer == str(offer.supplier_id):
                out.append(
                    FeatureScore(
                        Feature.SUPPLIER_SKU_EQUAL,
                        Decimal("0.50"),
                        f"Артикул поставщика {offer.supplier_sku}",
                    )
                )
            else:
                out.append(
                    FeatureScore(
                        Feature.SKU_OF_OTHER_SUPPLIER,
                        Decimal(0),
                        f"Артикул {offer.supplier_sku} принадлежит другому поставщику — "
                        "не доказательство",
                    )
                )
    code = offer.manufacturer_code
    product_code = entry.product.manufacturer_code
    if code and product_code and code_key(code) == code_key(product_code):
        out.append(
            FeatureScore(
                Feature.MANUFACTURER_CODE_EQUAL, Decimal("0.30"), f"Код производителя {code}"
            )
        )
    return out


def _fmt(values: Iterable[object]) -> str:
    return ", ".join(sorted(format(v, "f") if isinstance(v, Decimal) else str(v) for v in values))


def code_key(value: str) -> str:
    """Comparison key for codes: look-alike letters folded, separators dropped
    ("CM-101" in Latin, "СМ 101" in Cyrillic → "см101")."""
    return re.sub(r"[^0-9a-zа-я]", "", fold(value))


def _attribute_features(offer: NameFeatures, product: NameFeatures) -> list[FeatureScore]:
    out: list[FeatureScore] = []
    if offer.grades and product.grades:
        if offer.grades & product.grades:
            out.append(
                FeatureScore(
                    Feature.ATTRIBUTE_EQUAL,
                    Decimal("0.10"),
                    f"марка {_fmt(offer.grades & product.grades)}",
                )
            )
        # Same letter prefix, different number: М400 vs М500, D500 vs D600.
        clash = {
            (a, b)
            for a in offer.grades
            for b in product.grades
            if a != b and re.sub(r"\d", "", a) == re.sub(r"\d", "", b)
        }
        if clash:
            out.append(
                FeatureScore(
                    Feature.ATTRIBUTE_CONFLICT,
                    Decimal(0),
                    "разная марка: " + "; ".join(f"{a} ≠ {b}" for a, b in sorted(clash)),
                    blocking=True,
                )
            )
    if offer.dimensions and product.dimensions:
        if offer.dimensions & product.dimensions:
            out.append(FeatureScore(Feature.ATTRIBUTE_EQUAL, Decimal("0.10"), "размеры совпадают"))
        else:
            out.append(
                FeatureScore(
                    Feature.ATTRIBUTE_CONFLICT,
                    Decimal(0),
                    "разные размеры: "
                    + " ≠ ".join(
                        "; ".join("×".join(str(n) for n in d) for d in sorted(side))
                        for side in (offer.dimensions, product.dimensions)
                    ),
                    blocking=True,
                )
            )
    return out


def _measure_features(
    offer: NameFeatures, product: NameFeatures, pack_kinds: frozenset[str]
) -> list[FeatureScore]:
    """Measures of the same kind in both names. ``pack_kinds`` are measure kinds that are a
    pack size normalisable by the product's base unit (e.g. kg for a product sold in kg)."""
    out: list[FeatureScore] = []
    kinds = {k for k, _ in offer.measures} & {k for k, _ in product.measures}
    for kind in sorted(kinds):
        a = {v for k, v in offer.measures if k == kind}
        b = {v for k, v in product.measures if k == kind}
        if a & b:
            out.append(
                FeatureScore(Feature.ATTRIBUTE_EQUAL, Decimal("0.10"), f"{kind}: {_fmt(a & b)}")
            )
        elif kind in pack_kinds:
            out.append(
                FeatureScore(
                    Feature.PACK_DIFFERS_NORMALIZABLE,
                    Decimal(0),
                    f"другая фасовка ({_fmt(a)} ≠ {_fmt(b)}), цена приводится к базовой единице",
                )
            )
        else:
            code = (
                Feature.PACK_CONFLICT
                if kind in {"mass_kg", "volume_l", "count", "area_m2"}
                else (Feature.ATTRIBUTE_CONFLICT)
            )
            out.append(
                FeatureScore(code, Decimal(0), f"{kind}: {_fmt(a)} ≠ {_fmt(b)}", blocking=True)
            )
    return out


_KIND_OF_DIMENSION = {
    Dimension.MASS: "mass_kg",
    Dimension.VOLUME: "volume_l",
    Dimension.AREA: "area_m2",
    Dimension.LENGTH: "length_mm",
}


def _unit_feature(offer: OfferView, product: Product) -> list[FeatureScore]:
    if offer.unit is None:
        return []
    try:
        conversion_to(offer.unit, product.base_unit_code, offer.pack)
    except ConversionError:
        return [
            FeatureScore(
                Feature.UNIT_INCOMPATIBLE,
                Decimal("-0.30"),
                f"цена за «{offer.unit}», товар учитывается в «{product.base_unit_code}»; "
                "нужна фасовка",
            )
        ]
    return []


def score_candidate(offer: OfferView, entry: CatalogEntry) -> Candidate:
    product = entry.product
    offer_names = NameFeatures.of(offer.name)
    product_names = NameFeatures.of(product.canonical_name)
    features = _identifier_features(offer, entry)
    similarity = token_similarity(offer_names.tokens, product_names.tokens)
    if similarity:
        features.append(
            FeatureScore(
                Feature.NAME_TOKENS,
                (similarity * Decimal("0.60")).quantize(Decimal("0.01")),
                f"совпадение слов названия {similarity}",
            )
        )
    features += _attribute_features(offer_names, product_names)
    base_kind = _KIND_OF_DIMENSION.get(dimension_of(product.base_unit_code))
    features += _measure_features(
        offer_names, product_names, frozenset({base_kind} if base_kind else ())
    )
    features += _unit_feature(offer, product)
    total = sum((f.weight for f in features), Decimal(0))
    score = max(Decimal(0), min(Decimal(1), total)).quantize(Decimal("0.01"))
    if any(f.blocking for f in features):
        score = Decimal("0.00")
    return Candidate(offer.offer_id, product.id, score, tuple(features))


def candidates(
    offer: OfferView, catalog: Sequence[CatalogEntry], limit: int = 5
) -> list[Candidate]:
    """Ranked suggestions. Blocked candidates with strong textual evidence are kept (after
    the others) so the user sees *why* an obvious-looking match is not offered."""
    scored = [score_candidate(offer, e) for e in catalog if e.product.active]
    useful = [
        c
        for c in scored
        if c.score >= MIN_CANDIDATE_SCORE
        or (
            c.blocked
            and any(
                f.code is Feature.NAME_TOKENS and f.weight >= Decimal("0.30") for f in c.features
            )
        )
    ]
    useful.sort(key=lambda c: (c.blocked, -c.score, str(c.product_id)))
    return useful[:limit]


# --- decisions --------------------------------------------------------------------------------


class DecisionKind(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    REVOKE = "revoke"  # undo an earlier accept/reject (FR-15)


@dataclass(frozen=True, slots=True)
class MatchDecision:
    id: UUID
    offer_id: UUID
    product_id: UUID
    kind: DecisionKind
    actor: str  # "user:<id>" or "rule:<policy>@<version>"
    reason: str | None
    supersedes_id: UUID | None
    decided_at: datetime
    candidate: dict[str, object] | None  # the suggestion as it was shown (score, features)
    override: bool = False  # accepted although the matcher blocked it


@dataclass(slots=True)
class MatchHistory:
    """Append-only decisions of one offer; ``current`` is the latest non-superseded one."""

    offer_id: UUID
    decisions: list[MatchDecision] = field(default_factory=list)

    def _active(self) -> list[MatchDecision]:
        superseded = {d.supersedes_id for d in self.decisions if d.supersedes_id}
        return [
            d
            for d in self.decisions
            if d.id not in superseded and d.kind is not DecisionKind.REVOKE
        ]

    @property
    def accepted_product(self) -> UUID | None:
        accepted = [d for d in self._active() if d.kind is DecisionKind.ACCEPT]
        return accepted[-1].product_id if accepted else None

    def rejected_products(self) -> set[UUID]:
        return {d.product_id for d in self._active() if d.kind is DecisionKind.REJECT}

    def decide(
        self,
        *,
        kind: DecisionKind,
        product_id: UUID,
        actor: str,
        now: datetime,
        reason: str | None = None,
        candidate: Candidate | None = None,
    ) -> MatchDecision:
        reason_text = optional_text(reason, "reason", 1000)
        if candidate is not None and (
            candidate.offer_id != self.offer_id or candidate.product_id != product_id
        ):
            raise DomainValidationError("candidate", "Кандидат относится к другой паре.")
        previous = next((d for d in reversed(self._active()) if d.product_id == product_id), None)
        supersedes = previous.id if previous else None
        override = False
        if kind is DecisionKind.REVOKE:
            if previous is None:
                raise DomainValidationError("decision", "Нечего отменять для этого товара.")
        elif kind is DecisionKind.ACCEPT:
            current = self.accepted_product
            if current is not None and current != product_id:
                raise DomainValidationError(
                    "decision",
                    "Предложение уже сопоставлено с другим товаром: сначала отмените это "
                    "сопоставление.",
                )
            if candidate is not None and candidate.blocked:
                if reason_text is None:
                    raise DomainValidationError(
                        "reason",
                        "Сопоставитель нашёл конфликт (упаковка/атрибуты): подтверждение "
                        "требует причины.",
                    )
                override = True
            if actor.startswith("rule:") and (candidate is None or candidate.blocked):
                raise DomainValidationError("actor", "Правило не может подтверждать конфликт.")
        decision = MatchDecision(
            id=new_id(),
            offer_id=self.offer_id,
            product_id=product_id,
            kind=kind,
            actor=actor,
            reason=reason_text,
            supersedes_id=supersedes,
            decided_at=ensure_utc(now),
            candidate=candidate.to_json() if candidate else None,
            override=override,
        )
        self.decisions.append(decision)
        return decision


MIN_CALIBRATION_LINES = 200


@dataclass(frozen=True, slots=True)
class ThresholdCalibration:
    """Evidence that an auto-link threshold was checked on real, anonymised price lists.

    Synthetic fixtures do not qualify. A calibration is tied to the matcher version that was
    measured: any change of weights or features invalidates it."""

    dataset: str  # which anonymised price lists, where they are kept
    real_anonymised: bool
    matcher_version: str
    lines_evaluated: int
    wrong_links_at_threshold: int  # links above the threshold that a reviewer rejected
    approved_by: str
    approved_at: datetime

    @staticmethod
    def create(
        *,
        dataset: str,
        real_anonymised: bool,
        matcher_version: str,
        lines_evaluated: int,
        wrong_links_at_threshold: int,
        approved_by: str,
        approved_at: datetime,
    ) -> ThresholdCalibration:
        if not real_anonymised:
            raise DomainValidationError(
                "calibration.real_anonymised",
                "Порог проверяется на реальных обезличенных прайсах, не на синтетических.",
            )
        if lines_evaluated < MIN_CALIBRATION_LINES:
            raise DomainValidationError(
                "calibration.lines_evaluated",
                f"Для проверки порога нужно не меньше {MIN_CALIBRATION_LINES} строк.",
            )
        if wrong_links_at_threshold < 0 or wrong_links_at_threshold > lines_evaluated:
            raise DomainValidationError("calibration.wrong_links_at_threshold", "Некорректно.")
        dataset_text = optional_text(dataset, "calibration.dataset", 500)
        approver = optional_text(approved_by, "calibration.approved_by", 200)
        if dataset_text is None or approver is None:
            raise DomainValidationError("calibration", "Укажите набор данных и кто утвердил.")
        return ThresholdCalibration(
            dataset_text,
            True,
            matcher_version,
            lines_evaluated,
            wrong_links_at_threshold,
            approver,
            ensure_utc(approved_at),
        )


@dataclass(frozen=True, slots=True)
class AutoLinkPolicy:
    """FR-09: automatic linking. **Off** unless a threshold comes with a calibration on real
    anonymised price lists for the current matcher version; even then it links only with
    identifier evidence (GTIN or this supplier's SKU) and never repeats a rejection."""

    threshold: Decimal | None = None
    calibration: ThresholdCalibration | None = None
    name: str = "auto-link"

    @staticmethod
    def calibrated(threshold: Decimal, calibration: ThresholdCalibration) -> AutoLinkPolicy:
        if not Decimal("0.5") <= threshold <= 1:
            raise DomainValidationError("threshold", "Порог автосвязывания — от 0.5 до 1.")
        return AutoLinkPolicy(threshold, calibration)

    @property
    def enabled(self) -> bool:
        return (
            self.threshold is not None
            and self.calibration is not None
            and self.calibration.matcher_version == MATCHER_VERSION
        )

    def apply(
        self, history: MatchHistory, candidate: Candidate, now: datetime
    ) -> MatchDecision | None:
        if not self.enabled or self.threshold is None:
            return None
        if candidate.blocked or candidate.score < self.threshold:
            return None
        if candidate.matcher_version != MATCHER_VERSION:
            return None
        if not any(
            f.code in {Feature.GTIN_EQUAL, Feature.SUPPLIER_SKU_EQUAL} for f in candidate.features
        ):
            return None
        if (
            history.accepted_product is not None
            or candidate.product_id in history.rejected_products()
        ):
            return None
        return history.decide(
            kind=DecisionKind.ACCEPT,
            product_id=candidate.product_id,
            actor=f"rule:{self.name}@{MATCHER_VERSION}",
            now=now,
            reason=f"порог {self.threshold}",
            candidate=candidate,
        )
