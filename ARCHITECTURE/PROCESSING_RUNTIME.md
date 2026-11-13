# Runtime обработки

## Процессы
Desktop shell (Tauri) владеет окном, файловыми диалогами и безопасными разрешениями. UI — React/TypeScript. API/worker — FastAPI/Python с локальным persistent job store. OCR ресурсы включаются в поставку. Worker concurrency ограничивается CPU и памятью.

## Job state machine
`queued → validating → parsing → ocr (optional) → extracting → validating_result → review_required | completed`; боковые статусы: `failed`, `cancelled`, `retry_wait`. Переходы валидируются. Статус файла не равен статусу пакета.

## Идемпотентность и повтор
Ключ шага: document id + content hash + pipeline version + step config hash. Повтор после перезапуска продолжает с безопасной границы. Смена pipeline создаёт новую extraction run. Повтор не создаёт дубли строк.

## Локальная сеть и IPC
По умолчанию backend доступен только на loopback, порт динамический, capability токен передаётся защищённым способом из shell; CORS ограничен UI origin. Не принимать произвольные remote origins. Tauri allowlist/CSP минимальны.

## Cloud режим
Переход в облако — отдельная явная конфигурация workspace. Синхронизация объектов и конфликтов версионируется; локальные данные не удаляются автоматически. Очередь допускает offline, затем синхронизацию. MVP может отложить multi-device sync.
