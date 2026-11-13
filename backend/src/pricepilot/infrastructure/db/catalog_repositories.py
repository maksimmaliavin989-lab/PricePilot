"""SQL adapters for the product catalog and the audit log."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, select

from pricepilot.application.ports import Page, PageRequest
from pricepilot.domain.audit import AuditEvent
from pricepilot.domain.catalog import IdentifierScheme, Product, ProductIdentifier
from pricepilot.domain.common import search_key
from pricepilot.infrastructure.db.repositories import escape_like, paginate, stamp_of, to_page
from pricepilot.infrastructure.db.tables import audit_events, product_identifiers, products


def _product(row: Row[Any]) -> Product:
    return Product(
        id=row.id,
        workspace_id=row.workspace_id,
        canonical_name=row.canonical_name,
        brand=row.brand,
        manufacturer_code=row.manufacturer_code,
        attributes=dict(row.attributes_json),
        base_unit_code=row.base_unit_code,
        active=row.active,
        stamp=stamp_of(row),
    )


def _identifier(row: Row[Any]) -> ProductIdentifier:
    return ProductIdentifier(
        id=row.id,
        product_id=row.product_id,
        scheme=IdentifierScheme(row.scheme),
        value=row.value,
        normalized_value=row.normalized_value,
        issuer=row.issuer,
    )


class SqlCatalogRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    def add(self, p: Product, identifiers: list[ProductIdentifier]) -> None:
        self._c.execute(
            products.insert().values(
                id=p.id,
                workspace_id=p.workspace_id,
                canonical_name=p.canonical_name,
                name_key=p.name_key,
                brand=p.brand,
                manufacturer_code=p.manufacturer_code,
                attributes_json=p.attributes,
                base_unit_code=p.base_unit_code,
                active=p.active,
                created_at=p.stamp.created_at,
                updated_at=p.stamp.updated_at,
                version=p.stamp.version,
            )
        )
        if identifiers:
            self._c.execute(
                product_identifiers.insert(),
                [
                    {
                        "id": i.id,
                        "product_id": i.product_id,
                        "scheme": i.scheme.value,
                        "value": i.value,
                        "normalized_value": i.normalized_value,
                        "issuer": i.issuer,
                    }
                    for i in identifiers
                ],
            )

    def _identifiers(self, product_ids: list[UUID]) -> dict[UUID, list[ProductIdentifier]]:
        result: dict[UUID, list[ProductIdentifier]] = {pid: [] for pid in product_ids}
        if product_ids:
            for row in self._c.execute(
                select(product_identifiers)
                .where(product_identifiers.c.product_id.in_(product_ids))
                .order_by(product_identifiers.c.scheme, product_identifiers.c.normalized_value)
            ):
                result[row.product_id].append(_identifier(row))
        return result

    def get(self, product_id: UUID) -> tuple[Product, list[ProductIdentifier]] | None:
        row = self._c.execute(select(products).where(products.c.id == product_id)).first()
        if row is None:
            return None
        return _product(row), self._identifiers([product_id])[product_id]

    def list(
        self, workspace_id: UUID, page: PageRequest, *, query: str | None
    ) -> Page[tuple[Product, list[ProductIdentifier]]]:
        stmt = select(products).where(products.c.workspace_id == workspace_id)
        if query:
            stmt = stmt.where(
                products.c.name_key.like(f"%{escape_like(search_key(query))}%", escape="\\")
            )
        rows = list(self._c.execute(paginate(products, stmt, page)))
        found: Page[Product] = to_page(rows, page, _product)
        ids = self._identifiers([p.id for p in found.items])
        return Page(items=[(p, ids[p.id]) for p in found.items], next_cursor=found.next_cursor)


class SqlAuditRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    def add(self, e: AuditEvent) -> None:
        self._c.execute(
            audit_events.insert().values(
                id=e.id,
                workspace_id=e.workspace_id,
                actor_id=e.actor_id,
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                action=e.action,
                before_json=e.before,
                after_json=e.after,
                occurred_at=e.occurred_at,
                correlation_id=e.correlation_id,
            )
        )

    def list_for_entity(self, entity_type: str, entity_id: UUID) -> list[AuditEvent]:
        rows = self._c.execute(
            select(audit_events)
            .where(audit_events.c.entity_type == entity_type, audit_events.c.entity_id == entity_id)
            .order_by(audit_events.c.occurred_at, audit_events.c.id)
        )
        return [
            AuditEvent(
                id=r.id,
                workspace_id=r.workspace_id,
                actor_id=r.actor_id,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                action=r.action,
                before=r.before_json,
                after=r.after_json,
                occurred_at=r.occurred_at,
                correlation_id=r.correlation_id,
            )
            for r in rows
        ]
