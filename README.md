# PricePilot — инженерный пакет

**Версия:** 1.0 · **Язык:** русский · **Статус:** базовая спецификация для реализации

PricePilot — самостоятельное приложение для импорта прайсов поставщиков, приведения товарных позиций к единому виду, сопоставления аналогов и сравнения полной стоимости закупки. Продукт построен на общей платформе DIP (Document Intelligence Platform), но имеет собственную бизнес-логику и пользовательский интерфейс.

## Зафиксированные решения

- Продукт PricePilot — отдельная вертикаль закупок; EstimateCheck не является частью требований.
- Общая основа DIP: Document Intelligence Engine для приёма файлов, OCR, extraction, provenance, очередей и общих адаптеров.
- Архитектура приложения: модульный монолит.
- Desktop: Tauri 2 + React + TypeScript; backend: FastAPI/Python.
- PostgreSQL для облачного развёртывания, SQLite для локального режима.
- Local-first: обработка и хранение локально по умолчанию; cloud AI — опционально и только с явным выбором пользователя.
- Рынки: Россия и международный; валюты, локали, единицы и налоговые режимы моделируются явно.

## Навигация

- [Видение продукта](PRODUCT/PRODUCT_VISION.md), [требования](PRODUCT/PRODUCT_REQUIREMENTS.md), [сценарии](PRODUCT/USER_SCENARIOS.md)
- [Архитектура](ARCHITECTURE/SYSTEM_ARCHITECTURE.md), [DIP Document Engine](ARCHITECTURE/DOCUMENT_ENGINE.md), [runtime](ARCHITECTURE/PROCESSING_RUNTIME.md), [безопасность](ARCHITECTURE/SECURITY.md), [решения](ARCHITECTURE/ARCHITECTURE_DECISIONS.md)
- [Техническое задание](TECHNICAL_SPEC/TECHNICAL_SPECIFICATION.md), [схема БД](TECHNICAL_SPEC/DATABASE_SCHEMA.md), [API](TECHNICAL_SPEC/API_CONTRACTS.md), [модели](TECHNICAL_SPEC/DATA_MODELS.md), [ошибки](TECHNICAL_SPEC/ERROR_HANDLING.md)
- [Структура репозитория](DEVELOPMENT/REPOSITORY_STRUCTURE.md), [Sprint 01](DEVELOPMENT/SPRINT_01.md), [дорожная карта](DEVELOPMENT/DEVELOPMENT_ROADMAP.md), [тестирование](DEVELOPMENT/TESTING_STRATEGY.md), [приёмка](DEVELOPMENT/DEFINITION_OF_DONE.md)
- [Старт Codex](CODEX/CODEX_START_HERE.md), [правила](CODEX/IMPLEMENTATION_RULES.md), [задачи](CODEX/TASKS/)

## Разработка

Структура: `backend/` (FastAPI, модульный монолит: `domain` → `application` → `infrastructure`/`api`), `apps/webview/` (React + TS strict), `apps/desktop/src-tauri/` (Tauri 2 shell), `contracts/openapi/` (сгенерированный контракт), `scripts/`.

Требования: Node ≥ 20.19, [uv](https://docs.astral.sh/uv/) (Python 3.12), для desktop — Rust stable и [зависимости Tauri](https://v2.tauri.app/start/prerequisites/).

```bash
npm install && (cd backend && uv sync)
npm run dev            # API на 127.0.0.1:<случайный порт> + UI в браузере http://127.0.0.1:5173
npm run desktop:dev    # то же в окне Tauri (нужен Rust; запускать в интерактивном терминале — без TTY `tauri dev` сразу завершается)
npm run check          # форматирование, линтеры, типы, тесты, контракт OpenAPI, cargo fmt/clippy
npm run api:openapi    # после изменения API: обновить contracts/openapi и типы UI
```

**PostgreSQL:** совместимость проверена в CI на `postgres:17` (job `backend-postgres`, [run 43397872185](https://github.com/maksimmaliavin989-lab/PricePilot/actions/runs/43397872185)). Локальный прогон — только на отдельной одноразовой базе (тесты удаляют схему `public`): `PRICEPILOT_TEST_POSTGRES_URL=postgresql+psycopg://… uv run pytest`.

Данные dev-режима — `./.pricepilot-dev/` (в desktop — каталог данных приложения ОС). Сеть не нужна; внешние сервисы не вызываются.

## Как использовать

Сначала согласовать открытые продуктовые параметры в ТЗ (например, налоговые режимы, методику логистики и критерии MVP), затем выполнять задачи Codex последовательно. Каждое извлечённое значение должно оставаться проверяемым по исходному файлу. Этот пакет задаёт проектные требования, а не утверждает правовую или налоговую методику для конкретной страны.
