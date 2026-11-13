# Sprint 01 — Foundation и вертикальный срез

**Цель:** открыть desktop shell, создать локальный workspace, импортировать CSV/XLSX и показать строки с происхождением. Продолжительность — ориентировочно 2 недели; оценки уточняются после декомпозиции.

## Scope
1. Репозиторий, dev setup, CI formatting/type/lint.
2. Tauri 2 + React/TS стартовый экран и навигация.
3. FastAPI health, локальный startup/shutdown, loopback binding.
4. SQLite schema + Alembic baseline и workspace/supplier CRUD.
5. Immutable file store, checksum, import CSV/XLSX с безопасными лимитами.
6. Persistent processing job states; обработка в фоне.
7. Canonical extracted line/field с лист/строка/ячейка provenance.
8. Таблица review: исходная строка, редактирование, сохранение ревизии.
9. Synthetic fixtures и acceptance walkthrough.

## Out of scope
OCR/PDF, cloud sync, cloud AI, автоматический fuzzy matching, landed cost, роли cloud-команд, финальный экспорт PDF.

## Приёмка
- Приложение запускается без сети.
- Импорт корректного CSV/XLSX сохраняет hash и строки; повторный hash дедуплицируется.
- Неподдерживаемый/повреждённый/слишком большой файл получает ясную ошибку, исходник не теряется при безопасном допуске.
- Исправление строки сохраняет original + changed value + provenance и revision.
- Перезапуск приложения сохраняет job/document/workspace.
- В логе нет содержимого документа и секретов.

## Риски и зависимости
Проверить, как Tauri управляет Python sidecar на целевых ОС; выбрать упаковку backend и лицензии parser libs. Зафиксировать пределы файлов и test fixtures до начала реализации.
