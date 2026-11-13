"""Content-addressed, immutable store for original documents.

* Uploads are streamed into ``incoming/`` (same filesystem) while hashing and size-limiting.
* ``commit`` atomically renames the file to ``originals/<aa>/<bb>/<sha256>`` and makes it
  read-only. An existing original is never overwritten; identical bytes are stored once.
* ``read`` verifies the hash, so a tampered/corrupted original is detected, not parsed.
* Storage keys are validated: no user-controlled path ever reaches the filesystem.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from types import TracebackType
from typing import Self

from pricepilot.application.intake import IncomingFile, OriginalStoreError

_KEY_RE = re.compile(r"^originals/([0-9a-f]{2})/([0-9a-f]{2})/([0-9a-f]{64})$")


class StorageError(OriginalStoreError):
    """Base class; ``code`` is a stable error code."""

    code = "STORAGE_ERROR"


class UploadTooLargeError(StorageError):
    code = "LIMIT_EXCEEDED"


class StorageIntegrityError(StorageError):
    code = "STORAGE_ERROR"


class UploadWriter:
    """Streaming writer: ``write`` chunks, then ``finish`` to get an ``IncomingFile``."""

    def __init__(self, path: Path, max_bytes: int) -> None:
        self._path = path
        self._max = max_bytes
        self._hash = hashlib.sha256()
        self._size = 0
        self._fh = path.open("xb")
        self._done = False

    def write(self, chunk: bytes) -> None:
        self._size += len(chunk)
        if self._size > self._max:
            raise UploadTooLargeError
        self._hash.update(chunk)
        self._fh.write(chunk)

    def finish(self) -> IncomingFile:
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        self._done = True
        return IncomingFile(path=self._path, sha256=self._hash.hexdigest(), size=self._size)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._done:
            self._fh.close()
            self._path.unlink(missing_ok=True)


class LocalOriginalStore:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self._root = root
        self._incoming = root / "incoming"
        self._originals = root / "originals"
        self.max_bytes = max_bytes
        self._incoming.mkdir(parents=True, exist_ok=True)
        self._originals.mkdir(parents=True, exist_ok=True)

    def clean_incoming(self) -> int:
        """Remove partial uploads left by a crash. Originals are never touched."""
        removed = 0
        for leftover in self._incoming.glob("*.part"):
            leftover.unlink(missing_ok=True)
            removed += 1
        return removed

    def begin_upload(self) -> UploadWriter:
        return UploadWriter(self._incoming / f"{uuid.uuid4().hex}.part", self.max_bytes)

    @staticmethod
    def key_for(sha256: str) -> str:
        return f"originals/{sha256[:2]}/{sha256[2:4]}/{sha256}"

    def _path(self, key: str) -> Path:
        match = _KEY_RE.fullmatch(key)
        if match is None:
            raise StorageIntegrityError("invalid storage key")
        return self._root / key

    def commit(self, incoming: IncomingFile) -> str:
        key = self.key_for(incoming.sha256)
        target = self._path(key)
        try:
            if target.exists():
                incoming.path.unlink(missing_ok=True)  # identical bytes already stored
                return key
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(incoming.path, target)
            target.chmod(0o444)
        except OSError as exc:
            raise StorageError("cannot store original") from exc
        return key

    def discard(self, incoming: IncomingFile) -> None:
        incoming.path.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def read(self, key: str) -> bytes:
        path = self._path(key)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise StorageIntegrityError("original is missing") from exc
        if hashlib.sha256(data).hexdigest() != key.rsplit("/", 1)[1]:
            raise StorageIntegrityError("original does not match its hash")
        return data
