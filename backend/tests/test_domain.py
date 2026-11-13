from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from pricepilot.domain.common import (
    DomainValidationError,
    country_code,
    currency_code,
    display_name,
    ensure_utc,
    locale_tag,
    search_key,
)
from pricepilot.domain.suppliers import Supplier
from pricepilot.domain.workspaces import StorageMode, Workspace

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(("raw", "expected"), [("rub", "RUB"), (" usd ", "USD"), ("EUR", "EUR")])
def test_currency_code_normalised(raw: str, expected: str) -> None:
    assert currency_code(raw) == expected


@pytest.mark.parametrize("raw", ["", "RU", "RUBL", "РУБ", "12A"])
def test_currency_code_rejected(raw: str) -> None:
    with pytest.raises(DomainValidationError):
        currency_code(raw)


def test_country_code() -> None:
    assert country_code("ru") == "RU"
    with pytest.raises(DomainValidationError):
        country_code("RUS")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("ru_ru", "ru-RU"), ("en", "en"), ("sr-latn-rs", "sr-Latn-RS"), ("es-419", "es-419")],
)
def test_locale_tag(raw: str, expected: str) -> None:
    assert locale_tag(raw) == expected


@pytest.mark.parametrize("raw", ["", "russian", "ru-", "1x"])
def test_locale_tag_rejected(raw: str) -> None:
    with pytest.raises(DomainValidationError):
        locale_tag(raw)


def test_display_name_collapses_whitespace_and_rejects_control() -> None:
    assert display_name("  ООО   «Ромашка» ") == "ООО «Ромашка»"
    with pytest.raises(DomainValidationError):
        display_name("   ")
    with pytest.raises(DomainValidationError):
        display_name("a\x00b")
    with pytest.raises(DomainValidationError):
        display_name("x" * 201)


def test_search_key_is_unicode_case_insensitive() -> None:
    assert search_key("РОМАШКА") == search_key("ромашка")


def test_ensure_utc_rejects_naive_and_converts() -> None:
    with pytest.raises(DomainValidationError):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001 - intentionally naive
    msk = timezone(timedelta(hours=3))
    assert ensure_utc(datetime(2026, 1, 1, 3, tzinfo=msk)) == datetime(2026, 1, 1, tzinfo=UTC)


def test_workspace_create_and_change_bumps_version() -> None:
    ws = Workspace.create(
        name="Закупки", locale="ru-RU", base_currency="rub", country="ru", now=NOW
    )
    assert ws.storage_mode is StorageMode.LOCAL
    assert (ws.base_currency, ws.country_code, ws.stamp.version) == ("RUB", "RU", 1)
    later = NOW + timedelta(minutes=5)
    changed = ws.changed(now=later, name="Снабжение")
    assert changed.name == "Снабжение"
    assert changed.stamp.version == 2
    assert changed.stamp.created_at == NOW
    assert changed.stamp.updated_at == later
    assert changed.base_currency == "RUB"


def test_supplier_optional_fields_clear_and_archive() -> None:
    supplier = Supplier.create(
        workspace_id=uuid4(),
        name="Поставщик",
        country="ru",
        default_currency="rub",
        tax_id=" 7700000000 ",
        terms="",
        now=NOW,
    )
    assert supplier.tax_id == "7700000000"
    assert supplier.terms is None
    cleared = supplier.changed(now=NOW, tax_id=None)
    assert cleared.tax_id is None
    untouched = supplier.changed(now=NOW, name="Другой")
    assert untouched.tax_id == "7700000000"
    archived = supplier.changed(now=NOW, archived=True)
    assert archived.is_archived
    assert archived.archived_at == NOW
    restored = archived.changed(now=NOW, archived=False)
    assert not restored.is_archived
