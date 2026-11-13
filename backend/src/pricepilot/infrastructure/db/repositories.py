"""SQL repository adapters and unit of work."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, Select, Table, and_, or_, select

from pricepilot.application.ports import Page, PageCursor, PageRequest
from pricepilot.domain.common import AuditStamp, search_key
from pricepilot.domain.suppliers import Supplier
from pricepilot.domain.workspaces import StorageMode, Workspace
from pricepilot.infrastructure.db.tables import suppliers, workspaces


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def paginate(table: Table, stmt: Select[Any], page: PageRequest) -> Select[Any]:
    if page.after is not None:
        stmt = stmt.where(
            or_(
                table.c.created_at > page.after.created_at,
                and_(table.c.created_at == page.after.created_at, table.c.id > page.after.id),
            )
        )
    return stmt.order_by(table.c.created_at, table.c.id).limit(page.limit + 1)


def to_page[T](rows: list[Row[Any]], page: PageRequest, convert: Any) -> Page[T]:
    has_more = len(rows) > page.limit
    rows = rows[: page.limit]
    items: list[T] = [convert(row) for row in rows]
    cursor = None
    if has_more and rows:
        last = rows[-1]
        cursor = PageCursor(created_at=last.created_at, id=last.id)
    return Page(items=items, next_cursor=cursor)


def stamp_of(row: Row[Any]) -> AuditStamp:
    return AuditStamp(created_at=row.created_at, updated_at=row.updated_at, version=row.version)


def _workspace(row: Row[Any]) -> Workspace:
    return Workspace(
        id=row.id,
        name=row.name,
        locale=row.locale,
        base_currency=row.base_currency,
        country_code=row.country_code,
        storage_mode=StorageMode(row.storage_mode),
        stamp=stamp_of(row),
    )


def _supplier(row: Row[Any]) -> Supplier:
    return Supplier(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        country_code=row.country_code,
        default_currency=row.default_currency,
        tax_id=row.tax_id,
        terms=row.terms,
        archived_at=row.archived_at,
        stamp=stamp_of(row),
    )


class SqlWorkspaceRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    def add(self, workspace: Workspace) -> None:
        self._c.execute(
            workspaces.insert().values(
                id=workspace.id,
                name=workspace.name,
                locale=workspace.locale,
                base_currency=workspace.base_currency,
                country_code=workspace.country_code,
                storage_mode=workspace.storage_mode.value,
                settings_json={},
                created_at=workspace.stamp.created_at,
                updated_at=workspace.stamp.updated_at,
                version=workspace.stamp.version,
            )
        )

    def get(self, workspace_id: UUID) -> Workspace | None:
        row = self._c.execute(select(workspaces).where(workspaces.c.id == workspace_id)).first()
        return _workspace(row) if row else None

    def list(self, page: PageRequest) -> Page[Workspace]:
        rows = list(self._c.execute(paginate(workspaces, select(workspaces), page)))
        return to_page(rows, page, _workspace)

    def update(self, workspace: Workspace, expected_version: int) -> bool:
        result = self._c.execute(
            workspaces.update()
            .where(workspaces.c.id == workspace.id, workspaces.c.version == expected_version)
            .values(
                name=workspace.name,
                locale=workspace.locale,
                base_currency=workspace.base_currency,
                country_code=workspace.country_code,
                updated_at=workspace.stamp.updated_at,
                version=workspace.stamp.version,
            )
        )
        return result.rowcount == 1


class SqlSupplierRepository:
    def __init__(self, connection: Connection) -> None:
        self._c = connection

    def add(self, supplier: Supplier) -> None:
        self._c.execute(
            suppliers.insert().values(
                id=supplier.id,
                workspace_id=supplier.workspace_id,
                name=supplier.name,
                name_key=supplier.name_key,
                country_code=supplier.country_code,
                default_currency=supplier.default_currency,
                tax_id=supplier.tax_id,
                terms=supplier.terms,
                archived_at=supplier.archived_at,
                created_at=supplier.stamp.created_at,
                updated_at=supplier.stamp.updated_at,
                version=supplier.stamp.version,
            )
        )

    def get(self, supplier_id: UUID) -> Supplier | None:
        row = self._c.execute(select(suppliers).where(suppliers.c.id == supplier_id)).first()
        return _supplier(row) if row else None

    def list(
        self,
        workspace_id: UUID,
        page: PageRequest,
        *,
        query: str | None,
        include_archived: bool,
    ) -> Page[Supplier]:
        stmt = select(suppliers).where(suppliers.c.workspace_id == workspace_id)
        if not include_archived:
            stmt = stmt.where(suppliers.c.archived_at.is_(None))
        if query:
            pattern = f"%{escape_like(search_key(query))}%"
            stmt = stmt.where(suppliers.c.name_key.like(pattern, escape="\\"))
        rows = list(self._c.execute(paginate(suppliers, stmt, page)))
        return to_page(rows, page, _supplier)

    def update(self, supplier: Supplier, expected_version: int) -> bool:
        result = self._c.execute(
            suppliers.update()
            .where(suppliers.c.id == supplier.id, suppliers.c.version == expected_version)
            .values(
                name=supplier.name,
                name_key=supplier.name_key,
                country_code=supplier.country_code,
                default_currency=supplier.default_currency,
                tax_id=supplier.tax_id,
                terms=supplier.terms,
                archived_at=supplier.archived_at,
                updated_at=supplier.stamp.updated_at,
                version=supplier.stamp.version,
            )
        )
        return result.rowcount == 1
