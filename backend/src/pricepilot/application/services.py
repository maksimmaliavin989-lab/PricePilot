"""Use cases for workspaces and suppliers."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from pricepilot.application.errors import NotFoundError, VersionConflictError
from pricepilot.application.ports import (
    MAX_PAGE_SIZE,
    Clock,
    CorrelationProvider,
    Page,
    PageRequest,
    UnitOfWorkFactory,
)
from pricepilot.domain.audit import LOCAL_ACTOR, AuditEvent
from pricepilot.domain.common import optional_text, utc_now
from pricepilot.domain.suppliers import UNSET, Supplier
from pricepilot.domain.workspaces import Workspace


def _no_correlation() -> str | None:
    return None


def _changes(
    before: Workspace | Supplier, after: Workspace | Supplier, names: tuple[str, ...]
) -> dict[str, object]:
    """Safe audit payload: which fields changed (names only, no values) and the new version."""
    changed = [n for n in names if getattr(before, n) != getattr(after, n)]
    return {"changed": changed, "version": after.stamp.version}


def _bounded(page: PageRequest) -> PageRequest:
    return PageRequest(limit=max(1, min(page.limit, MAX_PAGE_SIZE)), after=page.after)


@dataclass(frozen=True, slots=True)
class WorkspacePatch:
    name: str | None = None
    locale: str | None = None
    base_currency: str | None = None
    country_code: str | None = None


@dataclass(frozen=True, slots=True)
class SupplierPatch:
    """Fields in ``clear`` are explicitly set to null; others are applied when not ``None``."""

    name: str | None = None
    country_code: str | None = None
    default_currency: str | None = None
    tax_id: str | None = None
    terms: str | None = None
    archived: bool | None = None
    provided: frozenset[str] = field(default_factory=frozenset)


class WorkspaceService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock = utc_now,
        correlation: CorrelationProvider = _no_correlation,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._correlation = correlation

    def _audit(
        self,
        workspace_id: UUID,
        entity: str,
        entity_id: UUID,
        action: str,
        after: dict[str, object],
    ) -> AuditEvent:
        return AuditEvent.record(
            workspace_id=workspace_id,
            actor_id=LOCAL_ACTOR,
            entity_type=entity,
            entity_id=entity_id,
            action=action,
            before=None,
            after=after,
            now=self._clock(),
            correlation_id=self._correlation(),
        )

    def create(self, *, name: str, locale: str, base_currency: str, country_code: str) -> Workspace:
        workspace = Workspace.create(
            name=name,
            locale=locale,
            base_currency=base_currency,
            country=country_code,
            now=self._clock(),
        )
        with self._uow() as uow:
            uow.workspaces.add(workspace)
            uow.audit.add(
                self._audit(workspace.id, "workspace", workspace.id, "create", {"version": 1})
            )
            uow.commit()
        return workspace

    def get(self, workspace_id: UUID) -> Workspace:
        with self._uow() as uow:
            workspace = uow.workspaces.get(workspace_id)
        if workspace is None:
            raise NotFoundError("workspace")
        return workspace

    def list(self, page: PageRequest) -> Page[Workspace]:
        with self._uow() as uow:
            return uow.workspaces.list(_bounded(page))

    def update(self, workspace_id: UUID, patch: WorkspacePatch, expected_version: int) -> Workspace:
        with self._uow() as uow:
            current = uow.workspaces.get(workspace_id)
            if current is None:
                raise NotFoundError("workspace")
            if current.stamp.version != expected_version:
                raise VersionConflictError("workspace", current.stamp.version)
            updated = current.changed(
                now=self._clock(),
                name=patch.name,
                locale=patch.locale,
                base_currency=patch.base_currency,
                country=patch.country_code,
            )
            if not uow.workspaces.update(updated, expected_version):
                latest = uow.workspaces.get(workspace_id)
                raise VersionConflictError("workspace", latest.stamp.version if latest else 0)
            uow.audit.add(
                self._audit(
                    workspace_id,
                    "workspace",
                    workspace_id,
                    "update",
                    _changes(current, updated, ("name", "locale", "base_currency", "country_code")),
                )
            )
            uow.commit()
        return updated


class SupplierService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock = utc_now,
        correlation: CorrelationProvider = _no_correlation,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._correlation = correlation

    def _audit(
        self,
        workspace_id: UUID,
        entity: str,
        entity_id: UUID,
        action: str,
        after: dict[str, object],
    ) -> AuditEvent:
        return AuditEvent.record(
            workspace_id=workspace_id,
            actor_id=LOCAL_ACTOR,
            entity_type=entity,
            entity_id=entity_id,
            action=action,
            before=None,
            after=after,
            now=self._clock(),
            correlation_id=self._correlation(),
        )

    def create(
        self,
        *,
        workspace_id: UUID,
        name: str,
        country_code: str,
        default_currency: str,
        tax_id: str | None = None,
        terms: str | None = None,
    ) -> Supplier:
        supplier = Supplier.create(
            workspace_id=workspace_id,
            name=name,
            country=country_code,
            default_currency=default_currency,
            tax_id=tax_id,
            terms=terms,
            now=self._clock(),
        )
        with self._uow() as uow:
            if uow.workspaces.get(workspace_id) is None:
                raise NotFoundError("workspace")
            uow.suppliers.add(supplier)
            uow.audit.add(
                self._audit(workspace_id, "supplier", supplier.id, "create", {"version": 1})
            )
            uow.commit()
        return supplier

    def get(self, supplier_id: UUID) -> Supplier:
        with self._uow() as uow:
            supplier = uow.suppliers.get(supplier_id)
        if supplier is None:
            raise NotFoundError("supplier")
        return supplier

    def list(
        self,
        workspace_id: UUID,
        page: PageRequest,
        *,
        query: str | None = None,
        include_archived: bool = False,
    ) -> Page[Supplier]:
        normalized_query = optional_text(query, "query", 200)
        with self._uow() as uow:
            if uow.workspaces.get(workspace_id) is None:
                raise NotFoundError("workspace")
            return uow.suppliers.list(
                workspace_id,
                _bounded(page),
                query=normalized_query,
                include_archived=include_archived,
            )

    def update(self, supplier_id: UUID, patch: SupplierPatch, expected_version: int) -> Supplier:
        with self._uow() as uow:
            current = uow.suppliers.get(supplier_id)
            if current is None:
                raise NotFoundError("supplier")
            if current.stamp.version != expected_version:
                raise VersionConflictError("supplier", current.stamp.version)
            updated = current.changed(
                now=self._clock(),
                name=patch.name,
                country=patch.country_code,
                default_currency=patch.default_currency,
                tax_id=patch.tax_id if "tax_id" in patch.provided else UNSET,
                terms=patch.terms if "terms" in patch.provided else UNSET,
                archived=patch.archived,
            )
            if not uow.suppliers.update(updated, expected_version):
                latest = uow.suppliers.get(supplier_id)
                raise VersionConflictError("supplier", latest.stamp.version if latest else 0)
            fields = ("name", "country_code", "default_currency", "tax_id", "terms", "archived_at")
            uow.audit.add(
                self._audit(
                    current.workspace_id,
                    "supplier",
                    supplier_id,
                    "update",
                    _changes(current, updated, fields),
                )
            )
            uow.commit()
        return updated
