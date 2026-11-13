"""Application-level errors with stable codes (mapped to HTTP by the API adapter)."""

from __future__ import annotations

from typing import Any


class ApplicationError(Exception):
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(ApplicationError):
    code = "NOT_FOUND"

    def __init__(self, entity: str) -> None:
        super().__init__("Запись не найдена.", {"entity": entity})


class VersionConflictError(ApplicationError):
    code = "VERSION_CONFLICT"

    def __init__(self, entity: str, current_version: int) -> None:
        super().__init__(
            "Запись уже изменена. Обновите данные и повторите правку.",
            {"entity": entity, "current_version": current_version},
        )


class InvalidCursorError(ApplicationError):
    code = "INVALID_CURSOR"

    def __init__(self) -> None:
        super().__init__("Некорректный курсор пагинации.")


class InvalidStateError(ApplicationError):
    code = "INVALID_STATE"

    def __init__(self, entity: str, current: str, target: str) -> None:
        super().__init__(
            "Действие недоступно в текущем состоянии записи.",
            {"entity": entity, "current": current, "target": target},
        )


class RevisionConflictError(ApplicationError):
    code = "VERSION_CONFLICT"

    def __init__(self, entity: str, current_revision: int) -> None:
        super().__init__(
            "Значение уже изменено. Обновите данные и повторите правку.",
            {"entity": entity, "current_revision": current_revision},
        )


class DuplicateExtractionError(ApplicationError):
    code = "INVALID_STATE"

    def __init__(self) -> None:
        super().__init__(
            "Строки этого запуска уже сохранены; повторная запись не создаёт дублей.",
            {"entity": "processing_run"},
        )
