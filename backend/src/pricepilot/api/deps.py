"""Request-scoped helpers: service lookup and optimistic-concurrency headers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import Request, Response

from pricepilot.api.errors import InvalidIfMatchError, PreconditionRequiredError
from pricepilot.application.catalog import CatalogService
from pricepilot.application.documents import DocumentService, ProcessingService
from pricepilot.application.review import ReviewService
from pricepilot.application.services import SupplierService, WorkspaceService

_IF_MATCH_RE = re.compile(r'^(?:W/)?"?(\d{1,9})"?$')


@dataclass(frozen=True, slots=True)
class Services:
    workspaces: WorkspaceService
    suppliers: SupplierService
    documents: DocumentService
    processing: ProcessingService
    review: ReviewService
    catalog: CatalogService


def services(request: Request) -> Services:
    container = request.app.state.services
    if not isinstance(container, Services):
        raise RuntimeError("services are not initialised")
    return container


def expected_version(if_match: str | None) -> int:
    if if_match is None:
        raise PreconditionRequiredError
    match = _IF_MATCH_RE.fullmatch(if_match.strip())
    if match is None:
        raise InvalidIfMatchError
    return int(match.group(1))


def set_etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'
