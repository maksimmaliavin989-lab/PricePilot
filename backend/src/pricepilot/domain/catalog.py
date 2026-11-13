"""Product Catalog module: canonical products and their identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pricepilot.domain.common import (
    AuditStamp,
    DomainValidationError,
    display_name,
    new_id,
    optional_text,
    search_key,
)
from pricepilot.domain.values import unit_code

MAX_ATTRIBUTES = 50


class IdentifierScheme(StrEnum):
    GTIN = "gtin"  # GTIN-8/12/13/14 (EAN/UPC); stored as GTIN-14
    SUPPLIER_SKU = "supplier_sku"  # meaningful only together with the issuing supplier
    MANUFACTURER_CODE = "manufacturer_code"
    OTHER = "other"


def gtin_check_digit_ok(digits: str) -> bool:
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def normalize_identifier(scheme: IdentifierScheme, value: str) -> str:
    raw = value.strip()
    if not raw:
        raise DomainValidationError("identifiers.value", "Идентификатор не может быть пустым.")
    if scheme is IdentifierScheme.GTIN:
        digits = re.sub(r"[\s-]", "", raw)
        if not digits.isdigit() or len(digits) not in {8, 12, 13, 14}:
            raise DomainValidationError("identifiers.value", "GTIN: 8, 12, 13 или 14 цифр.")
        if not gtin_check_digit_ok(digits):
            raise DomainValidationError("identifiers.value", "GTIN: неверная контрольная цифра.")
        return digits.zfill(14)
    normalized = re.sub(r"\s+", "", search_key(raw))
    if len(normalized) > 128:
        raise DomainValidationError("identifiers.value", "Идентификатор длиннее 128 символов.")
    return normalized


@dataclass(frozen=True, slots=True)
class ProductIdentifier:
    id: UUID
    product_id: UUID
    scheme: IdentifierScheme
    value: str
    normalized_value: str
    issuer: str | None

    @staticmethod
    def create(
        *, product_id: UUID, scheme: IdentifierScheme, value: str, issuer: str | None
    ) -> ProductIdentifier:
        issuer_text = optional_text(issuer, "identifiers.issuer", 200)
        if scheme is IdentifierScheme.SUPPLIER_SKU and issuer_text is None:
            raise DomainValidationError(
                "identifiers.issuer",
                "Для артикула поставщика укажите поставщика: один SKU не равен товару у других.",
            )
        return ProductIdentifier(
            id=new_id(),
            product_id=product_id,
            scheme=scheme,
            value=value.strip(),
            normalized_value=normalize_identifier(scheme, value),
            issuer=issuer_text,
        )


@dataclass(frozen=True, slots=True)
class Product:
    id: UUID
    workspace_id: UUID
    canonical_name: str
    brand: str | None
    manufacturer_code: str | None
    attributes: dict[str, str]
    base_unit_code: str
    active: bool
    stamp: AuditStamp

    @property
    def name_key(self) -> str:
        return search_key(self.canonical_name)

    @staticmethod
    def create(
        *,
        workspace_id: UUID,
        canonical_name: str,
        brand: str | None,
        manufacturer_code: str | None,
        attributes: dict[str, str],
        base_unit: str,
        now: datetime,
    ) -> Product:
        if len(attributes) > MAX_ATTRIBUTES:
            raise DomainValidationError("attributes", f"Не более {MAX_ATTRIBUTES} атрибутов.")
        clean: dict[str, str] = {}
        for key, value in attributes.items():
            k = optional_text(key, "attributes", 100)
            v = optional_text(value, "attributes", 500)
            if k is None or v is None:
                raise DomainValidationError("attributes", "Пустой ключ или значение атрибута.")
            clean[k] = v
        return Product(
            id=new_id(),
            workspace_id=workspace_id,
            canonical_name=display_name(canonical_name, "canonical_name", 300),
            brand=optional_text(brand, "brand", 200),
            manufacturer_code=optional_text(manufacturer_code, "manufacturer_code", 128),
            attributes=clean,
            base_unit_code=unit_code(base_unit, "base_unit_code"),
            active=True,
            stamp=AuditStamp.initial(now),
        )
