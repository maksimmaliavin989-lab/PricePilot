"""FastAPI composition root for the local API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from sqlalchemy import Engine
from starlette.middleware.cors import CORSMiddleware

from pricepilot import __version__
from pricepilot.api.deps import Services
from pricepilot.api.errors import install_error_handlers
from pricepilot.api.middleware import (
    CORRELATION_HEADER,
    LocalGuardMiddleware,
    RequestContextMiddleware,
)
from pricepilot.api.routers import catalog, documents, fields, health, runs, suppliers, workspaces
from pricepilot.api.schemas import ErrorResponse
from pricepilot.application.catalog import CatalogService
from pricepilot.application.documents import DocumentService, ProcessingService
from pricepilot.application.intake import ImportService, PipelineRunner
from pricepilot.application.review import ReviewService
from pricepilot.application.services import SupplierService, WorkspaceService
from pricepilot.application.source import SourceService
from pricepilot.config import ALLOWED_HOST_NAMES, MAX_JSON_BODY_BYTES, Settings
from pricepilot.dip.adapters.registry import ParserRegistry
from pricepilot.dip.limits import DEFAULT_LIMITS
from pricepilot.infrastructure.db.engine import build_engine, sqlite_url
from pricepilot.infrastructure.db.migrate import upgrade_to_head
from pricepilot.infrastructure.db.uow import SqlUnitOfWork
from pricepilot.infrastructure.logging import correlation_id_var, register_secret
from pricepilot.infrastructure.storage.file_store import LocalOriginalStore
from pricepilot.infrastructure.worker import InlineWorker, ThreadWorker

API_PREFIX = "/api/v1"
logger = logging.getLogger("pricepilot.api")

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (400, 401, 403, 404, 409, 413, 422, 428, 500)
}


@dataclass(frozen=True, slots=True)
class Runtime:
    services: Services
    store: LocalOriginalStore
    worker: InlineWorker | ThreadWorker


def build_runtime(
    engine: Engine,
    data_dir: Path,
    *,
    max_upload_bytes: int = DEFAULT_LIMITS.max_file_bytes,
    inline_worker: bool = True,
) -> Runtime:
    def uow() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine)

    correlation = correlation_id_var.get
    limits = replace(DEFAULT_LIMITS, max_file_bytes=max_upload_bytes)
    store = LocalOriginalStore(data_dir, max_bytes=limits.max_file_bytes)
    registry = ParserRegistry()
    documents = DocumentService(uow, correlation=correlation)
    processing = ProcessingService(uow, correlation=correlation)
    runner = PipelineRunner(uow, documents, processing, store, registry, limits)
    worker: InlineWorker | ThreadWorker = (
        InlineWorker(runner) if inline_worker else ThreadWorker(runner)
    )
    services = Services(
        workspaces=WorkspaceService(uow, correlation=correlation),
        suppliers=SupplierService(uow, correlation=correlation),
        documents=documents,
        processing=processing,
        review=ReviewService(uow, correlation=correlation),
        catalog=CatalogService(uow, correlation=correlation),
        imports=ImportService(documents, processing, store, registry, limits, worker.submit),
        source=SourceService(documents, store, registry, limits),
    )
    return Runtime(services=services, store=store, worker=worker)


def build_services(engine: Engine, data_dir: Path) -> Services:
    """Services with a synchronous worker (tests and tooling)."""
    return build_runtime(engine, data_dir).services


def create_app(settings: Settings, on_ready: Callable[[], None] | None = None) -> FastAPI:
    register_secret(settings.api_token)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = build_engine(sqlite_url(settings.database_path))
        upgrade_to_head(engine)
        app.state.engine = engine
        runtime = build_runtime(
            engine,
            settings.data_dir,
            max_upload_bytes=settings.max_upload_bytes,
            inline_worker=settings.worker_mode == "inline",
        )
        runtime.store.clean_incoming()
        app.state.services = runtime.services
        app.state.store = runtime.store
        runtime.worker.start()
        logger.info("api started", extra={"event": "startup"})
        if on_ready is not None:
            on_ready()
        try:
            yield
        finally:
            runtime.worker.stop()
            engine.dispose()
            logger.info("api stopped", extra={"event": "shutdown"})

    app = FastAPI(
        title="PricePilot Local API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        responses=_ERROR_RESPONSES,
    )
    install_error_handlers(app)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(workspaces.router, prefix=API_PREFIX)
    app.include_router(suppliers.router, prefix=API_PREFIX)
    app.include_router(documents.router, prefix=API_PREFIX)
    app.include_router(runs.router, prefix=API_PREFIX)
    app.include_router(fields.router, prefix=API_PREFIX)
    app.include_router(catalog.router, prefix=API_PREFIX)

    # Order: last added is outermost → RequestContext → CORS → LocalGuard → routes.
    app.add_middleware(
        LocalGuardMiddleware,
        token=settings.api_token,
        allowed_hosts=ALLOWED_HOST_NAMES,
        allowed_origins=frozenset(settings.allowed_origins),
        max_body_bytes=MAX_JSON_BODY_BYTES,
        upload_paths=frozenset({f"{API_PREFIX}/documents"}),
        max_upload_bytes=settings.max_upload_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=[
            "authorization",
            "content-type",
            "if-match",
            "x-file-name",
            CORRELATION_HEADER,
        ],
        expose_headers=["etag", CORRELATION_HEADER],
        allow_credentials=False,
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)
    return app


def openapi_document(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
        description=(
            "Локальный API PricePilot. Доступен только на 127.0.0.1, требует "
            "`Authorization: Bearer <token>`, выданный оболочкой при запуске."
        ),
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["LocalToken"] = {
        "type": "http",
        "scheme": "bearer",
    }
    schema["security"] = [{"LocalToken": []}]
    return schema
