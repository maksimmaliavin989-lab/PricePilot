"""Content-based format detection (never trust the extension alone).

Outcome codes follow ERROR_HANDLING.md:
* ``UNSUPPORTED_FORMAT`` — unknown content or an extension outside the allowlist (e.g. DOC);
* ``INVALID_CONTENT`` — empty file, damaged container, or content that contradicts the
  extension (MIME spoofing);
* ``PASSWORD_REQUIRED`` — encrypted container;
* ``LIMIT_EXCEEDED`` — decompression-bomb guards for ZIP containers.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from pricepilot.dip.limits import Limits
from pricepilot.dip.model import MIME_TYPES, FileKind

_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")

EXTENSIONS: dict[str, FileKind] = {
    "csv": FileKind.CSV,
    "txt": FileKind.CSV,
    "xlsx": FileKind.XLSX,
    "xls": FileKind.XLS,
    "docx": FileKind.DOCX,
    "doc": FileKind.DOC,
    "pdf": FileKind.PDF,
    "jpg": FileKind.JPEG,
    "jpeg": FileKind.JPEG,
    "png": FileKind.PNG,
    "tif": FileKind.TIFF,
    "tiff": FileKind.TIFF,
    "heic": FileKind.HEIC,
}
# Recognised formats the product will never parse (explicit, honest error).
PERMANENTLY_UNSUPPORTED = frozenset({FileKind.DOC, FileKind.UNKNOWN})


@dataclass(frozen=True, slots=True)
class SniffResult:
    kind: FileKind
    mime: str
    problem: str | None = None  # stable error code, None when the file is acceptable
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None


def _fail(kind: FileKind, code: str, message: str) -> SniffResult:
    return SniffResult(kind=kind, mime=MIME_TYPES[kind], problem=code, message=message)


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = head.decode("cp1251")
        except UnicodeDecodeError:  # pragma: no cover - cp1251 decodes nearly everything
            return False
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t")
    return controls <= max(1, len(text) // 200)


def _zip_kind(path: Path, limits: Limits) -> SniffResult:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_zip_entries:
                return _fail(FileKind.UNKNOWN, "LIMIT_EXCEEDED", "Слишком много файлов в архиве.")
            total = 0
            for info in infos:
                if info.flag_bits & 0x1:
                    return _fail(FileKind.UNKNOWN, "PASSWORD_REQUIRED", "Файл защищён паролем.")
                total += info.file_size
                ratio = info.file_size / max(info.compress_size, 1)
                if total > limits.max_zip_uncompressed_bytes or ratio > limits.max_zip_ratio:
                    return _fail(
                        FileKind.UNKNOWN,
                        "LIMIT_EXCEEDED",
                        "Содержимое файла разворачивается в слишком большой объём.",
                    )
            names = {i.filename for i in infos}
    except (zipfile.BadZipFile, OSError, ValueError):
        return _fail(FileKind.UNKNOWN, "INVALID_CONTENT", "Файл повреждён: не читается архив.")
    if "[Content_Types].xml" in names:
        if any(n.startswith("xl/") for n in names):
            return SniffResult(FileKind.XLSX, MIME_TYPES[FileKind.XLSX])
        if any(n.startswith("word/") for n in names):
            return SniffResult(FileKind.DOCX, MIME_TYPES[FileKind.DOCX])
    return SniffResult(FileKind.UNKNOWN, MIME_TYPES[FileKind.UNKNOWN])


def detect(path: Path, limits: Limits) -> SniffResult:
    """Identify the content of ``path`` regardless of its name."""
    with path.open("rb") as fh:
        head = fh.read(8192)
    if not head:
        return _fail(FileKind.UNKNOWN, "INVALID_CONTENT", "Файл пустой.")
    if head.startswith(b"PK\x03\x04"):
        return _zip_kind(path, limits)
    if head.startswith(b"%PDF-"):
        return SniffResult(FileKind.PDF, MIME_TYPES[FileKind.PDF])
    if head.startswith(b"\xff\xd8\xff"):
        return SniffResult(FileKind.JPEG, MIME_TYPES[FileKind.JPEG])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return SniffResult(FileKind.PNG, MIME_TYPES[FileKind.PNG])
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return SniffResult(FileKind.TIFF, MIME_TYPES[FileKind.TIFF])
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
        return SniffResult(FileKind.HEIC, MIME_TYPES[FileKind.HEIC])
    if head.startswith(_OLE2):
        data = path.read_bytes() if path.stat().st_size <= 32 * 1024 * 1024 else head
        if _ENCRYPTED_PACKAGE in data:
            return _fail(FileKind.UNKNOWN, "PASSWORD_REQUIRED", "Файл защищён паролем.")
        # Legacy XLS and DOC share the OLE2 container; the extension decides which one.
        return SniffResult(FileKind.XLS, MIME_TYPES[FileKind.XLS])
    if _looks_like_text(head):
        return SniffResult(FileKind.CSV, MIME_TYPES[FileKind.CSV])
    return SniffResult(FileKind.UNKNOWN, MIME_TYPES[FileKind.UNKNOWN])


def classify(path: Path, extension: str, limits: Limits) -> SniffResult:
    """Combine content detection with the extension allowlist."""
    detected = detect(path, limits)
    if detected.problem is not None:
        return detected
    expected = EXTENSIONS.get(extension.lower().lstrip("."))
    if expected is None:
        return _fail(
            detected.kind,
            "UNSUPPORTED_FORMAT",
            "Формат файла не поддерживается. Поддерживаются CSV, XLSX, XLS, DOCX, PDF, "
            "JPG, PNG, TIFF, HEIC.",
        )
    if detected.kind is FileKind.XLS and expected is FileKind.DOC:
        detected = SniffResult(FileKind.DOC, MIME_TYPES[FileKind.DOC])
    if detected.kind is FileKind.UNKNOWN:
        return _fail(
            FileKind.UNKNOWN, "UNSUPPORTED_FORMAT", "Не удалось определить формат содержимого."
        )
    if detected.kind is not expected:
        return _fail(
            detected.kind,
            "INVALID_CONTENT",
            f"Содержимое файла ({detected.kind.value.upper()}) не соответствует расширению "
            f".{extension.lower().lstrip('.')}.",
        )
    if detected.kind is FileKind.DOC:
        return _fail(
            FileKind.DOC,
            "UNSUPPORTED_FORMAT",
            "Старый формат DOC не поддерживается. Сохраните файл как DOCX или PDF.",
        )
    return detected
