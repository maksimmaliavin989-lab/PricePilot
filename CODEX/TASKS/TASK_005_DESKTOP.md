# TASK 005 — Desktop packaging и безопасный runtime

Собрать Tauri 2 desktop shell с FastAPI sidecar, bundled Python/runtime и локальными OCR resources. Определить startup, shutdown, crash recovery, migration prompt, app data path и backup/restore. Минимизировать Tauri capabilities, CSP и сетевые origins. Подготовить Windows/macOS package pipeline и подпись как release-stage requirement.

**Приёмка:** запуск/выход/повторный запуск, отсутствие сети, sidecar crash recovery, порт только loopback, чистая установка и upgrade без потери локальных данных.
