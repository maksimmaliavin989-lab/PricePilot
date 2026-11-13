"""Parser adapter contract."""

from __future__ import annotations

from typing import Protocol

from pricepilot.dip.limits import Limits
from pricepilot.dip.model import FileKind, TableDocument


class Parser(Protocol):
    """Turns original bytes into the canonical table model.

    Implementations must: never execute macros or formulas, enforce ``limits``, raise
    ``ParseError`` with a stable code instead of returning an empty "successful" result,
    and never log document content.
    """

    name: str
    version: str
    kinds: frozenset[FileKind]

    def parse(self, data: bytes, limits: Limits) -> TableDocument: ...
