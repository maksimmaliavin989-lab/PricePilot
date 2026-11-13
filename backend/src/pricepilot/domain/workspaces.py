"""Identity & Workspace module: local workspaces."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pricepilot.domain.common import (
    AuditStamp,
    country_code,
    currency_code,
    display_name,
    locale_tag,
    new_id,
)


class StorageMode(StrEnum):
    """Only local storage exists in the MVP. Cloud mode needs a separate explicit decision."""

    LOCAL = "local"


@dataclass(frozen=True, slots=True)
class Workspace:
    id: UUID
    name: str
    locale: str
    base_currency: str
    country_code: str
    storage_mode: StorageMode
    stamp: AuditStamp

    @staticmethod
    def create(
        *, name: str, locale: str, base_currency: str, country: str, now: datetime
    ) -> Workspace:
        return Workspace(
            id=new_id(),
            name=display_name(name),
            locale=locale_tag(locale),
            base_currency=currency_code(base_currency, "base_currency"),
            country_code=country_code(country),
            storage_mode=StorageMode.LOCAL,
            stamp=AuditStamp.initial(now),
        )

    def changed(
        self,
        *,
        now: datetime,
        name: str | None = None,
        locale: str | None = None,
        base_currency: str | None = None,
        country: str | None = None,
    ) -> Workspace:
        return replace(
            self,
            name=display_name(name) if name is not None else self.name,
            locale=locale_tag(locale) if locale is not None else self.locale,
            base_currency=(
                currency_code(base_currency, "base_currency")
                if base_currency is not None
                else self.base_currency
            ),
            country_code=country_code(country) if country is not None else self.country_code,
            stamp=self.stamp.bumped(now),
        )
