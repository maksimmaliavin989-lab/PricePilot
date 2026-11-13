"""Clean-up of OCR text artefacts before layout.

PP-OCR recognition models sometimes emit LaTeX-like markup for superscripts
("M{3 }$" for "м³"). Recognisable superscripts are restored; markup that lost its
content ("m{ }$") is kept visibly broken ("m?") so the value is not silently read as
another unit and the line goes to review.
"""

from __future__ import annotations

import re

_SUPERSCRIPT = re.compile(r"\$?\{\s*\^?\s*([23])\s*\}\s*\$?")
_EMPTY_MARKUP = re.compile(r"\$?\{\s*\}\s*\$?")
_STRAY = re.compile(r"[{}$]")
_SUPERSCRIPTS = {"2": "²", "3": "³"}


def clean_ocr_text(text: str) -> tuple[str, bool]:
    """Returns the cleaned text and whether OCR markup had to be removed (``suspicious``)."""
    cleaned = _SUPERSCRIPT.sub(lambda m: _SUPERSCRIPTS[m.group(1)], text)
    cleaned = _EMPTY_MARKUP.sub("?", cleaned)
    cleaned = _STRAY.sub("", cleaned).strip()
    return cleaned, cleaned != text.strip()
