"""SQLite backups, pre-migration safety copies, restore and the data-directory lock.

* Backups use SQLite's online backup API, so they are consistent while the API runs.
* Originals (``originals/``) are immutable and content-addressed; a database backup does not
  copy them. A full copy of the data directory is the complete backup.
* ``DataDirLock`` is an OS-level lock (released automatically if the process dies): one API
  per data directory, and no restore underneath a running API.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Self

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from pricepilot.infrastructure.db.migrate import alembic_config

BACKUP_DIR = "backups"
_NAME_RE = re.compile(r"^pricepilot-[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,40}\.sqlite3$")


class BackupError(Exception):
    code = "STORAGE_ERROR"


class AlreadyRunningError(BackupError):
    code = "ALREADY_RUNNING"


@dataclass(frozen=True, slots=True)
class BackupInfo:
    name: str
    size_bytes: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MigrationReport:
    from_revision: str
    to_revision: str
    backup: str  # backup file name inside ``backups/``


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision(engine: Engine) -> str:
    head = ScriptDirectory.from_config(alembic_config(engine)).get_current_head()
    if head is None:  # pragma: no cover - migrations always exist
        raise BackupError("no migrations")
    return head


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def create_backup(database: Path, data_dir: Path, label: str) -> BackupInfo:
    """Consistent copy of ``database`` into ``data_dir/backups``."""
    if not database.exists():
        raise BackupError("database does not exist")
    target_dir = data_dir / BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")[:40] or "manual"
    target = target_dir / f"pricepilot-{_stamp()}-{safe_label}.sqlite3"
    suffix = 1
    while target.exists():
        suffix += 1
        target = target_dir / f"pricepilot-{_stamp()}-{safe_label}-{suffix}.sqlite3"
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    stat = target.stat()
    return BackupInfo(target.name, stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC))


def list_backups(data_dir: Path) -> list[BackupInfo]:
    folder = data_dir / BACKUP_DIR
    if not folder.exists():
        return []
    items = [
        BackupInfo(p.name, p.stat().st_size, datetime.fromtimestamp(p.stat().st_mtime, UTC))
        for p in folder.iterdir()
        if p.is_file() and _NAME_RE.fullmatch(p.name)
    ]
    return sorted(items, key=lambda b: b.name, reverse=True)


def backup_before_migration(
    engine: Engine, database: Path, data_dir: Path
) -> MigrationReport | None:
    """If an existing database is behind the code, copy it before upgrading."""
    if not database.exists():
        return None
    current = current_revision(engine)
    head = head_revision(engine)
    if current is None or current == head:
        return None
    info = create_backup(database, data_dir, f"before-{head}")
    return MigrationReport(from_revision=current, to_revision=head, backup=info.name)


def restore_backup(name: str, database: Path, data_dir: Path) -> BackupInfo:
    """Replace the database with a backup. The current database is backed up first.
    Must run while no API holds the data-directory lock."""
    if not _NAME_RE.fullmatch(name):
        raise BackupError("invalid backup name")
    source = data_dir / BACKUP_DIR / name
    if not source.is_file():
        raise BackupError("backup not found")
    check = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise BackupError("backup is damaged")
    finally:
        check.close()
    if database.exists():
        create_backup(database, data_dir, "before-restore")
    for leftover in (Path(f"{database}-wal"), Path(f"{database}-shm")):
        leftover.unlink(missing_ok=True)
    tmp = database.with_suffix(".restore-tmp")
    shutil.copy2(source, tmp)
    os.replace(tmp, database)
    stat = database.stat()
    return BackupInfo(source.name, stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC))


class DataDirLock:
    """Exclusive OS lock on ``data_dir/api.lock``."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "api.lock"
        self._fh: IO[str] | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fh = self._path.open("a+")
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise AlreadyRunningError("data directory is in use") from exc
        self._fh = fh

    def release(self) -> None:
        if self._fh is not None:
            self._fh.close()  # closing the descriptor releases the lock on every OS
            self._fh = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
