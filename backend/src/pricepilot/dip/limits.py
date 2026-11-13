"""Resource limits for intake and parsing (SECURITY.md: size, rows, decompression)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class Limits:
    max_file_bytes: int = 50 * 1024 * 1024
    max_rows: int = 100_000  # per document, all sheets together
    max_columns: int = 200
    max_cell_chars: int = 10_000
    max_sheets: int = 50
    # ZIP containers (XLSX/DOCX): defence against decompression bombs.
    max_zip_entries: int = 2_000
    max_zip_uncompressed_bytes: int = 250 * 1024 * 1024
    max_zip_ratio: int = 200

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


DEFAULT_LIMITS = Limits()
