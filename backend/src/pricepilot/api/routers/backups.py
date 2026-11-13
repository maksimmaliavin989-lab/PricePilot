from __future__ import annotations

from fastapi import APIRouter, Request, status

from pricepilot.api.schemas import BackupOut
from pricepilot.config import Settings
from pricepilot.infrastructure.db.backup import create_backup, list_backups

router = APIRouter(prefix="/backups", tags=["system"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.get("", response_model=list[BackupOut], summary="Резервные копии базы (новые сначала)")
def get_backups(request: Request) -> list[BackupOut]:
    return [
        BackupOut(name=b.name, size_bytes=b.size_bytes, created_at=b.created_at)
        for b in list_backups(_settings(request).data_dir)
    ]


@router.post(
    "",
    response_model=BackupOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать резервную копию базы (SQLite online backup; оригиналы не копируются)",
)
def post_backup(request: Request) -> BackupOut:
    settings = _settings(request)
    info = create_backup(settings.database_path, settings.data_dir, "manual")
    return BackupOut(name=info.name, size_bytes=info.size_bytes, created_at=info.created_at)
