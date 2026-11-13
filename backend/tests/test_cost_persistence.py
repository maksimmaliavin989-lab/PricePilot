"""TASK_006 storage on every engine (SQLite always, PostgreSQL in CI): policy, FX, verified
tax rates and comparisons survive a round trip with exact decimals and dates."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pricepilot.api.deps import Services
from pricepilot.application.errors import VersionConflictError
from pricepilot.domain.costs import Basis, Category, ComponentType, CostPolicy, FxRate
from pricepilot.domain.values import Quantity


def test_cost_data_round_trip(svc: Services) -> None:
    ws = svc.workspaces.create(name="W", locale="ru-RU", base_currency="RUB", country_code="RU")
    assert svc.costs.policy(ws.id).version == 0
    unloading = ComponentType.create(
        code="unloading", label="Разгрузка", category=Category.CHARGE, basis=Basis.PER_UNIT
    )
    policy = CostPolicy.default().extended(unloading)
    saved = svc.costs.save_policy(ws.id, name="Своя", types=policy.types, expected_version=0)
    assert saved.version == 1
    loaded = svc.costs.policy(ws.id)
    assert loaded.version == 1
    assert loaded.policy.fingerprint == CostPolicy.create("Своя", policy.types).fingerprint
    with pytest.raises(VersionConflictError):
        svc.costs.save_policy(ws.id, name="Своя", types=policy.types, expected_version=0)

    rate = FxRate.create(
        base="USD",
        quote="RUB",
        rate=Decimal("92.12345678"),
        effective_date=date(2026, 11, 12),
        source="ЦБ РФ",
    )
    svc.costs.add_fx(ws.id, rate)
    assert [r for _, r in svc.costs.list_fx(ws.id)] == [rate]

    source = svc.costs.add_rate_source(
        ws.id,
        name="Справочник",
        reference="Приказ № 1",
        verified_by="Бухгалтер",
        verified_at=datetime(2026, 1, 10, tzinfo=UTC),
        rates=[("vat", Decimal("20"), date(2026, 1, 1), date(2026, 12, 31), None)],
    )
    stored = svc.costs.rate_sources(ws.id)
    assert stored[0].source == source.source
    assert stored[0].rates[0].rate == Decimal("20")
    assert stored[0].rates[0].valid_to == date(2026, 12, 31)

    product = svc.catalog.create(workspace_id=ws.id, canonical_name="Цемент", base_unit_code="kg")
    view = svc.costs.create_comparison(
        ws.id, name="Сравнение", target_currency="RUB", as_of=date(2026, 11, 13)
    )
    view = svc.costs.add_item(
        view.comparison.id,
        product_id=product.product.id,
        required=Quantity(Decimal("1000.5"), "kg"),
    )
    assert view.items[0].item.required == Quantity(Decimal("1000.5"), "kg")
    assert view.comparison.as_of == date(2026, 11, 13)
