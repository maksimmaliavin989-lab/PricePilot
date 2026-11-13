# TASK 001 — Foundation и локальный workspace

**Цель:** scaffold desktop UI + API, работающий офлайн.

## Задачи
- Создать структуру desktop/frontend/backend/domain.
- Настроить Tauri 2, React/TypeScript strict и FastAPI app lifecycle.
- Запустить API на loopback с безопасным случайным портом и токеном.
- SQLite + migration baseline; workspace/supplier CRUD.
- Настроить dev scripts и CI lint/type/format.

## Приёмка
Приложение открывается локально без сети; health endpoint недоступен извне; workspace и supplier сохраняются после перезапуска; миграция создаёт чистую базу; ошибки имеют correlation id.

## Не делать
Не строить облачный login, синхронизацию и cloud AI.
