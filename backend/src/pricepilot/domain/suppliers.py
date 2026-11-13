"""Supplier Registry module: suppliers of a workspace."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from pricepilot.domain.common import (
    AuditStamp,
    country_code,
    currency_code,
    display_name,
    ensure_utc,
    new_id,
    optional_text,
    search_key,
)

TAX_ID_MAX = 64
TERMS_MAX = 4000

_UNSET: object = object()


@dataclass(frozen=True, slots=True)
class Supplier:
    id: UUID
    workspace_id: UUID
    name: str
    country_code: str
    default_currency: str
    tax_id: str | None
    terms: str | None
    archived_at: datetime | None
    stamp: AuditStamp

    @property
    def name_key(self) -> str:
        return search_key(self.name)

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @staticmethod
    def create(
        *,
        workspace_id: UUID,
        name: str,
        country: str,
        default_currency: str,
        tax_id: str | None,
        terms: str | None,
        now: datetime,
    ) -> Supplier:
        return Supplier(
            id=new_id(),
            workspace_id=workspace_id,
            name=display_name(name),
            country_code=country_code(country),
            default_currency=currency_code(default_currency, "default_currency"),
            tax_id=optional_text(tax_id, "tax_id", TAX_ID_MAX),
            terms=optional_text(terms, "terms", TERMS_MAX),
            archived_at=None,
            stamp=AuditStamp.initial(now),
        )

    def changed(
        self,
        *,
        now: datetime,
        name: str | None = None,
        country: str | None = None,
        default_currency: str | None = None,
        tax_id: str | object | None = _UNSET,
        terms: str | object | None = _UNSET,
        archived: bool | None = None,
    ) -> Supplier:
        """Return a new revision. ``tax_id``/``terms`` accept ``None`` to clear the value."""
        archived_at = self.archived_at
        if archived is True and archived_at is None:
            archived_at = ensure_utc(now)
        elif archived is False:
            archived_at = None
        return replace(
            self,
            name=display_name(name) if name is not None else self.name,
            country_code=country_code(country) if country is not None else self.country_code,
            default_currency=(
                currency_code(default_currency, "default_currency")
                if default_currency is not None
                else self.default_currency
            ),
            tax_id=(
                self.tax_id
                if tax_id is _UNSET
                else optional_text(_as_optional_str(tax_id), "tax_id", TAX_ID_MAX)
            ),
            terms=(
                self.terms
                if terms is _UNSET
                else optional_text(_as_optional_str(terms), "terms", TERMS_MAX)
            ),
            archived_at=archived_at,
            stamp=self.stamp.bumped(now),
        )


def _as_optional_str(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError("expected str or None")


UNSET = _UNSET
