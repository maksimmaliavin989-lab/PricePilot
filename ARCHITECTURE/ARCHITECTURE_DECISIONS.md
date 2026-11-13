# Архитектурные решения (ADR)

## ADR-001: Модульный монолит
**Статус:** принято. Ускоряет MVP и сохраняет возможность отделить worker/модули позднее. Модули имеют владельцев данных и публичные интерфейсы.

## ADR-002: Tauri 2 + React/TypeScript, FastAPI/Python
**Статус:** принято по решению владельца. Python удобен для локального OCR/парсеров; Tauri даёт desktop shell; API сохраняет опцию cloud deployment.

## ADR-003: SQLite local, PostgreSQL cloud
**Статус:** принято. Нужны portability discipline, тесты миграций и избегание engine-specific бизнес-семантики.

## ADR-004: Local-first, cloud AI opt-in
**Статус:** принято. Сетевой доступ не обязателен для базового workflow. Передача документов должна быть объяснена и разрешена явно.

## ADR-005: Общая платформа DIP, отдельный PricePilot
**Статус:** принято. Общие document intake/OCR/extraction/provenance могут быть переиспользованы. Бизнес-модели закупки отдельны. EstimateCheck не входит в продуктовый пакет требований.

## ADR-006: Оригинал неизменяем, corrections additive
**Статус:** принято. Extraction run и user correction создают версию/событие, обеспечивая воспроизводимость.

## ADR-007: Библиотеки foundation (TASK_001)
**Статус:** принято для MVP, пересмотреть при упаковке (TASK_005). Всё работает офлайн, без телеметрии.
- Backend: FastAPI (MIT), Starlette (BSD-3), Uvicorn (BSD-3), Pydantic (MIT), SQLAlchemy 2 Core (MIT), Alembic (MIT), platformdirs (MIT). FastAPI тянет `opentelemetry-api` (Apache-2.0) — только API, без экспортёров и сетевой отправки. Dev: pytest (MIT), httpx (BSD-3), ruff (MIT), mypy (MIT); окружение — uv.
- UI: React 19 (MIT), Vite 8 (MIT), TypeScript 5.9 (Apache-2.0; 5.x, так как openapi-typescript/typescript-eslint ещё не поддерживают 7.x), openapi-typescript (MIT), ESLint + typescript-eslint (MIT), Prettier (MIT), Vitest (MIT), `@tauri-apps/api` (MIT/Apache-2.0).
- Desktop: Tauri 2 (MIT/Apache-2.0), serde/serde_json, getrandom (MIT/Apache-2.0). Плагины Tauri не подключены.
- PostgreSQL-драйвер: psycopg 3 (`psycopg[binary]`, LGPL-3.0, динамическая библиотека) — только extra `postgres` и dev-зависимость; локальная сборка его не требует.
- ORM-классы не используются: таблицы описаны SQLAlchemy Core, доменные сущности — dataclasses; репозитории в `infrastructure/db`.

## ADR-008: Запуск локального API и handshake
**Статус:** принято. Shell генерирует 256-битный токен на запуск и передаёт его в stdin процесса API (не в argv/env/файлах). API привязан только к `127.0.0.1` без параметра хоста; порт `0` выбирает ОС; готовность — JSON-строка в stdout. Stdin остаётся открытым: EOF (выход или падение shell) завершает API. Дополнительно: проверка `Host` и `Origin`, CORS только для UI origins, лимит тела. UI получает адрес и токен командой Tauri `backend_connection`; в браузерном dev-режиме токен добавляет Vite proxy, в бандл он не попадает. Миграции выполняются автоматически при старте; диалог миграции и backup — TASK_005.

## Нерешённые ADR
Выбор OCR движка/лицензии, конкретная модель синхронизации, политика курсов FX, налоговые/таможенные правила по юрисдикциям, cloud provider, SSO/командные роли. До решения использовать локальный интерфейс и явные вводимые параметры.
