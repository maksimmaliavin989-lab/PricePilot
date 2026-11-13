"""Unit of work: one database transaction per use case across module repositories."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy import Connection, Engine

from pricepilot.infrastructure.db.catalog_repositories import (
    SqlAuditRepository,
    SqlCatalogRepository,
)
from pricepilot.infrastructure.db.document_repositories import (
    SqlDocumentRepository,
    SqlExtractionRepository,
    SqlRunRepository,
)
from pricepilot.infrastructure.db.repositories import SqlSupplierRepository, SqlWorkspaceRepository


class SqlUnitOfWork:
    """One transaction per use case. Rolled back unless ``commit`` is called."""

    workspaces: SqlWorkspaceRepository
    suppliers: SqlSupplierRepository
    documents: SqlDocumentRepository
    runs: SqlRunRepository
    extraction: SqlExtractionRepository
    catalog: SqlCatalogRepository
    audit: SqlAuditRepository

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None

    def __enter__(self) -> Self:
        self._connection = self._engine.connect()
        self._connection.begin()
        self.workspaces = SqlWorkspaceRepository(self._connection)
        self.suppliers = SqlSupplierRepository(self._connection)
        self.documents = SqlDocumentRepository(self._connection)
        self.runs = SqlRunRepository(self._connection)
        self.extraction = SqlExtractionRepository(self._connection)
        self.catalog = SqlCatalogRepository(self._connection)
        self.audit = SqlAuditRepository(self._connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._connection is not None  # noqa: S101 - programming error guard
        try:
            if self._connection.in_transaction():
                self._connection.rollback()
        finally:
            self._connection.close()
            self._connection = None

    def commit(self) -> None:
        assert self._connection is not None  # noqa: S101 - programming error guard
        self._connection.commit()
