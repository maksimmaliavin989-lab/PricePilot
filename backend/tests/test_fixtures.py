"""Integrity of the synthetic price-list fixtures shared by parser/OCR tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "price-lists"
MANIFEST = FIXTURES / "manifest.json"
IGNORED = {"manifest.json", "README.md", ".DS_Store"}

# Content signatures — the same checks a MIME sniffer will rely on.
SIGNATURES = {
    "xlsx": b"PK\x03\x04",
    "docx": b"PK\x03\x04",
    "pdf": b"%PDF-",
    "jpg": b"\xff\xd8\xff",
    "png": b"\x89PNG\r\n\x1a\n",
}


def _manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return data


def _entries() -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = _manifest()["files"]
    return files


def test_manifest_lists_exactly_the_files_on_disk() -> None:
    on_disk = {
        p.relative_to(FIXTURES).as_posix()
        for p in FIXTURES.rglob("*")
        if p.is_file() and p.name not in IGNORED
    }
    listed = {e["path"] for e in _entries()}
    assert on_disk == listed


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: str(e["path"]))
def test_fixture_checksum_size_and_signature(entry: dict[str, Any]) -> None:
    data = (FIXTURES / entry["path"]).read_bytes()
    assert len(data) == entry["size_bytes"]
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert data.startswith(SIGNATURES[entry["format"]])


def test_fixtures_are_byte_unique() -> None:
    hashes = [e["sha256"] for e in _entries()]
    assert len(hashes) == len(set(hashes))


def test_coverage_of_formats_layouts_and_tiers() -> None:
    entries = _entries()
    assert {e["format"] for e in entries} == set(SIGNATURES)
    assert {e["layout"] for e in entries} == {"classic", "wholesale_retail", "catalog"}
    assert {e["tier"] for e in entries} == {None, "promo", "wholesale", "premium"}
    assert any(e["kind"] == "scan" and e["format"] == "pdf" for e in entries)
