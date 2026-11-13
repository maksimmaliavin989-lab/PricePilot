"""FastAPI composition root for the local API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
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
from pricepilot.application.review import ReviewService
from pricepilot.application.services import SupplierService, WorkspaceService
from pricepilot.config import ALLOWED_HOST_NAMES, MAX_JSON_BODY_BYTES, Settings
from pricepilot.infrastructure.db.engine import build_engine, sqlite_url
from pricepilot.infrastructure.db.migrate import upgrade_to_head
from pricepilot.infrastructure.db.uow import SqlUnitOfWork
from pricepilot.infrastructure.logging import correlation_id_var, register_secret

API_PREFIX = "/api/v1"
logger = logging.getLogger("pricepilot.api")

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (400, 401, 403, 404, 409, 413, 422, 428, 500)
}


def build_services(engine: Engine) -> Services:
    def uow() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine)

    correlation = correlation_id_var.get
    return Services(
        workspaces=WorkspaceService(uow, correlation=correlation),
        suppliers=SupplierService(uow, correlation=correlation),
        documents=DocumentService(uow, correlation=correlation),
        processing=ProcessingService(uow, correlation=correlation),
        review=ReviewService(uow, correlation=correlation),
        catalog=CatalogService(uow, correlation=correlation),
    )


def create_app(settings: Settings, on_ready: Callable[[], None] | None = None) -> FastAPI:
    register_secret(settings.api_token)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = build_engine(sqlite_url(settings.database_path))
        upgrade_to_head(engine)
        app.state.engine = engine
        app.state.services = build_services(engine)
        logger.info("api started", extra={"event": "startup"})
        if on_ready is not None:
            on_ready()
        try:
            yield
        finally:
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
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["authorization", "content-type", "if-match", CORRELATION_HEADER],
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
