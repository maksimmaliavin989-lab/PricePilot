# TASK 002 — Схема данных и API домена

Реализовать модели из TECHNICAL_SPEC/DATABASE_SCHEMA.md и базовые контракты документов/поставщиков/каталога. Начать с workspace, supplier, source_document, processing_run, extracted_line/field. Добавить миграции SQLite и PostgreSQL, repository ports, DTO и OpenAPI.

**Приёмка:** суммы и количества сериализуются без потери точности; версии обновления защищены optimistic concurrency; проверки FK/unique; cursor pagination; контрактные тесты и миграция чистой базы.
