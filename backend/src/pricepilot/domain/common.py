"""Shared domain value objects and errors.

Rules here are engine- and transport-agnostic: no SQL, HTTP or UI concerns.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4


class DomainValidationError(ValueError):
    """A value violates a domain rule. ``field`` is the public attribute name."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def new_id() -> UUID:
    return uuid4()


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(moment: datetime) -> datetime:
    """Moments in time must be timezone-aware; they are normalised to UTC."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise DomainValidationError("timestamp", "Момент времени должен содержать часовой пояс.")
    return moment.astimezone(UTC)


_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
# Pragmatic BCP 47 subset: language[-Script][-REGION|-digits][-variant...]
_LOCALE_RE = re.compile(
    r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?(?:-[A-Za-z0-9]{5,8})*$"
)


def currency_code(value: str, field: str = "currency") -> str:
    """ISO 4217-like alphabetic code. Full registry check is a separate decision (FX ADR)."""
    normalized = value.strip().upper()
    if not _CURRENCY_RE.fullmatch(normalized):
        raise DomainValidationError(field, "Код валюты должен состоять из трёх латинских букв.")
    return normalized


def country_code(value: str, field: str = "country_code") -> str:
    """ISO 3166-1 alpha-2-like code."""
    normalized = value.strip().upper()
    if not _COUNTRY_RE.fullmatch(normalized):
        raise DomainValidationError(field, "Код страны должен состоять из двух латинских букв.")
    return normalized


def locale_tag(value: str, field: str = "locale") -> str:
    """BCP 47 tag, canonical casing (``ru-RU``, ``en``, ``sr-Latn-RS``)."""
    parts = value.strip().replace("_", "-").split("-")
    canonical: list[str] = []
    for index, part in enumerate(parts):
        if index == 0:
            canonical.append(part.lower())
        elif len(part) == 4 and part.isalpha():
            canonical.append(part.title())
        elif len(part) == 2 and part.isalpha():
            canonical.append(part.upper())
        else:
            canonical.append(part)
    normalized = "-".join(canonical)
    if not _LOCALE_RE.fullmatch(normalized):
        raise DomainValidationError(field, "Локаль должна быть тегом BCP 47, например ru-RU.")
    return normalized


def _has_control_chars(value: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in value)


def display_name(value: str, field: str = "name", max_length: int = 200) -> str:
    normalized = unicodedata.normalize("NFC", " ".join(value.split()))
    if not normalized:
        raise DomainValidationError(field, "Название не может быть пустым.")
    if len(normalized) > max_length:
        raise DomainValidationError(field, f"Название длиннее {max_length} символов.")
    if _has_control_chars(normalized):
        raise DomainValidationError(field, "Название содержит управляющие символы.")
    return normalized


def optional_text(value: str | None, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise DomainValidationError(field, f"Значение длиннее {max_length} символов.")
    if _has_control_chars(normalized):
        raise DomainValidationError(field, "Значение содержит управляющие символы.")
    return normalized


def search_key(value: str) -> str:
    """Locale-independent case-insensitive key (works for Cyrillic, unlike SQL ``lower``)."""
    return unicodedata.normalize("NFKC", value).casefold()


@dataclass(frozen=True, slots=True)
class AuditStamp:
    created_at: datetime
    updated_at: datetime
    version: int

    @staticmethod
    def initial(now: datetime) -> AuditStamp:
        now = ensure_utc(now)
        return AuditStamp(created_at=now, updated_at=now, version=1)

    def bumped(self, now: datetime) -> AuditStamp:
        return AuditStamp(
            created_at=self.created_at, updated_at=ensure_utc(now), version=self.version + 1
        )
