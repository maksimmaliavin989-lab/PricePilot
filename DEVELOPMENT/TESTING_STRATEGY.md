# Стратегия тестирования

## Пирамида
- Unit: нормализация, единицы, валюты, стоимость, матчер feature scoring, state machine.
- Contract: OpenAPI schema, API errors, pagination, idempotency.
- Integration: репозитории SQLite и PostgreSQL; migrations from empty/previous version; filesystem store; processing queue.
- Parser regression: versioned fixtures per format, expected rows/fields/provenance; malformed/encrypted/oversized inputs.
- End-to-end: import → review → correction → compare → export в offline desktop build.
- Security: archive bombs, path traversal names, formula injection exports, MIME spoofing, huge images, parser timeout, local API unauthorized origin.

## Качество extraction/matching
Подготовить обезличенный benchmark с точной разметкой: формат, язык, качество скана, поле, координаты. Метрики отдельно по supplier SKU, name, quantity/pack, price, currency, tax. Измерять precision/recall, confidence calibration, coverage и время проверки. Для matching — precision@k и false merge rate; ошибочное объединение критичнее пропущенного кандидата.

## Правила fixtures
Не добавлять настоящие конфиденциальные прайсы без письменного разрешения. Использовать синтетические или обезличенные примеры. Закреплять эталонную версию pipeline и допускаемые отклонения. Проверять, что отказ parser не выдаёт ложный completed.

## Матрица
SQLite и PostgreSQL; Windows/macOS; русский/английский; online/offline; supported/unsupported; user accepted/corrected/rejected.
