"""Pure ASGI middleware: correlation IDs, access log and the local security guard."""

from __future__ import annotations

import hmac
import json
import logging
import re
import time
import uuid
from collections.abc import Iterable
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pricepilot.api.errors import error_body
from pricepilot.infrastructure.logging import correlation_id_var

logger = logging.getLogger("pricepilot.api")
CORRELATION_HEADER = "x-correlation-id"
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", ()):
        if key == name:
            return str(value.decode("latin-1"))
    return None


async def _send_json(
    send: Send, status: int, body: dict[str, Any], extra_headers: Iterable[tuple[bytes, bytes]] = ()
) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
        *extra_headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class RequestContextMiddleware:
    """Outermost layer. Assigns a correlation id to every request and logs a safe access record
    (method, path without query string, status, duration)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, CORRELATION_HEADER.encode())
        correlation_id = (
            incoming if incoming and _CORRELATION_RE.fullmatch(incoming) else str(uuid.uuid4())
        )
        token = correlation_id_var.set(correlation_id)
        started = time.perf_counter()
        state = {"status": 500, "started": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["started"] = True
                headers = list(message.get("headers", []))
                headers.append((CORRELATION_HEADER.encode(), correlation_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            logger.error("unhandled error", exc_info=exc, extra={"error_code": "INTERNAL_ERROR"})
            if not state["started"]:
                await _send_json(
                    send_wrapper,
                    500,
                    error_body("INTERNAL_ERROR", "Внутренняя ошибка. Повторите действие."),
                )
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": state["status"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            correlation_id_var.reset(token)


class LocalGuardMiddleware:
    """Rejects requests that did not come from the PricePilot shell/UI:

    * ``Host`` must be loopback (DNS-rebinding protection);
    * ``Origin``, when present, must be an allowed UI origin;
    * ``Authorization: Bearer <token>`` must match the per-launch capability token;
    * JSON bodies are size-limited.

    CORS preflight is answered by ``CORSMiddleware`` placed outside this layer.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        allowed_hosts: frozenset[str],
        allowed_origins: frozenset[str],
        max_body_bytes: int,
        upload_paths: frozenset[str] = frozenset(),
        max_upload_bytes: int = 0,
    ) -> None:
        self.app = app
        self._token = token.encode()
        self._hosts = allowed_hosts
        self._origins = allowed_origins
        self._max_body = max_body_bytes
        self._upload_paths = upload_paths
        self._max_upload = max_upload_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        host_header = _header(scope, b"host") or ""
        hostname = host_header.rsplit(":", 1)[0] if ":" in host_header else host_header
        if hostname not in self._hosts:
            await _send_json(send, 400, error_body("INVALID_HOST", "Недопустимый адрес API."))
            return

        origin = _header(scope, b"origin")
        if origin is not None and origin not in self._origins:
            await _send_json(
                send, 403, error_body("FORBIDDEN_ORIGIN", "Запрос из недоверенного источника.")
            )
            return

        auth = _header(scope, b"authorization") or ""
        scheme, _, presented = auth.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.strip().encode(), self._token
        ):
            await _send_json(
                send,
                401,
                error_body("UNAUTHORIZED", "Нет доступа к локальному API."),
                [(b"www-authenticate", b"Bearer")],
            )
            return

        limit = self._max_body
        if scope.get("method") == "POST" and scope.get("path") in self._upload_paths:
            limit = self._max_upload
        if scope.get("method") in _BODY_METHODS:
            length = _header(scope, b"content-length")
            if length is not None and (not length.isdigit() or int(length) > limit):
                await _send_json(
                    send,
                    413,
                    error_body(
                        "LIMIT_EXCEEDED",
                        "Слишком большой запрос.",
                        {"max_bytes": limit},
                    ),
                )
                return
            receive = self._limited_receive(receive, limit)

        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BodyTooLargeError:
            if started:
                raise
            await _send_json(
                send,
                413,
                error_body("LIMIT_EXCEEDED", "Слишком большой запрос.", {"max_bytes": limit}),
            )

    def _limited_receive(self, receive: Receive, limit: int) -> Receive:
        received = 0

        async def wrapper() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLargeError
            return message

        return wrapper


class BodyTooLargeError(Exception):
    pass
