"""Adapter registry and the DIP pipeline version.

Formats that are recognised but have no adapter yet (PDF, DOCX, XLS, images) get an explicit
``UNSUPPORTED_FORMAT`` failure: processing is never imitated. Adding an adapter bumps
``PIPELINE_VERSION``, which creates new runs instead of overwriting old results.
"""

from __future__ import annotations

import hashlib
import json

from pricepilot.dip.adapters.base import Parser
from pricepilot.dip.adapters.csv_adapter import CsvParser
from pricepilot.dip.adapters.xlsx_adapter import XlsxParser
from pricepilot.dip.limits import Limits
from pricepilot.dip.model import MIME_TYPES, FileKind

# 0.2.0: typed price columns (опт/розница/акция) no longer require a per-line choice.
PIPELINE_VERSION = "dip-0.2.0"

PLANNED_KINDS: dict[FileKind, str] = {
    FileKind.PDF: "PDF",
    FileKind.DOCX: "DOCX",
    FileKind.XLS: "XLS",
    FileKind.JPEG: "JPG",
    FileKind.PNG: "PNG",
    FileKind.TIFF: "TIFF",
    FileKind.HEIC: "HEIC",
}


class ParserRegistry:
    def __init__(self, parsers: list[Parser] | None = None) -> None:
        self._parsers: list[Parser] = parsers or [CsvParser(), XlsxParser()]

    def for_kind(self, kind: FileKind) -> Parser | None:
        for parser in self._parsers:
            if kind in parser.kinds:
                return parser
        return None

    def for_mime(self, mime: str) -> tuple[FileKind, Parser | None]:
        for kind, value in MIME_TYPES.items():
            if value == mime:
                return kind, self.for_kind(kind)
        return FileKind.UNKNOWN, None

    def config_hash(self, limits: Limits) -> str:
        payload = {
            "pipeline": PIPELINE_VERSION,
            "limits": limits.fingerprint(),
            "parsers": sorted(f"{p.name}@{p.version}" for p in self._parsers),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
