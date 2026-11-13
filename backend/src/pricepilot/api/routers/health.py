from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import Engine, text

from pricepilot import __version__
from pricepilot.api.schemas import HealthOut

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut, summary="Состояние локального API")
def health(request: Request) -> HealthOut:
    engine: Engine = request.app.state.engine
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return HealthOut(
        status="ok",
        version=__version__,
        api_version="v1",
        storage_mode="local",
        database="ok",
    )
