"""Stable error envelope: ``{"error": {code, message, details, correlation_id}}``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from pricepilot.application.errors import ApplicationError
from pricepilot.domain.common import DomainValidationError
from pricepilot.infrastructure.logging import correlation_id_var

logger = logging.getLogger("pricepilot.api.errors")

_STATUS_BY_CODE = {
    "NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "INVALID_STATE": 409,
    "INVALID_CURSOR": 400,
}

_HTTP_CODES = {
    400: ("BAD_REQUEST", "Некорректный запрос."),
    404: ("NOT_FOUND", "Ресурс не найден."),
    405: ("METHOD_NOT_ALLOWED", "Метод не поддерживается."),
}


class PreconditionRequiredError(Exception):
    pass


class InvalidIfMatchError(Exception):
    pass


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "correlation_id": correlation_id_var.get() or "",
        }
    }


def error_response(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(error_body(code, message, details), status_code=status, headers=headers)


def _validation_details(exc: RequestValidationError) -> dict[str, Any]:
    # Never echo submitted values back (``input``/``ctx`` may contain user data).
    fields = [
        {
            "loc": [str(part) for part in err.get("loc", ())],
            "type": str(err.get("type", "")),
            "msg": str(err.get("msg", "")),
        }
        for err in exc.errors()
    ]
    return {"fields": fields}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def _application(_: Request, exc: ApplicationError) -> JSONResponse:
        return error_response(
            _STATUS_BY_CODE.get(exc.code, 400), exc.code, exc.message, exc.details
        )

    @app.exception_handler(DomainValidationError)
    async def _domain(_: Request, exc: DomainValidationError) -> JSONResponse:
        return error_response(
            422,
            "VALIDATION_ERROR",
            exc.message,
            {"fields": [{"loc": ["body", exc.field], "type": "domain", "msg": exc.message}]},
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            422, "VALIDATION_ERROR", "Проверьте заполнение полей.", _validation_details(exc)
        )

    @app.exception_handler(PreconditionRequiredError)
    async def _precondition(_: Request, __: PreconditionRequiredError) -> JSONResponse:
        return error_response(
            428,
            "PRECONDITION_REQUIRED",
            "Для изменения нужен заголовок If-Match с текущей версией записи.",
        )

    @app.exception_handler(InvalidIfMatchError)
    async def _bad_if_match(_: Request, __: InvalidIfMatchError) -> JSONResponse:
        return error_response(
            400, "INVALID_IF_MATCH", "Заголовок If-Match должен содержать версию."
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _HTTP_CODES.get(exc.status_code, ("HTTP_ERROR", "Ошибка запроса."))
        return error_response(exc.status_code, code, message)

    @app.exception_handler(SQLAlchemyError)
    async def _database(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("database error", exc_info=exc, extra={"error_code": "DATABASE_ERROR"})
        return error_response(
            500, "DATABASE_ERROR", "Ошибка локальной базы данных. Повторите действие."
        )
