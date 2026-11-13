# API-контракты v1

Base path `/api/v1`. JSON UTF-8. OpenAPI — источник синхронизации UI. Каждая ошибка: `{ "error": {"code":"...", "message":"...", "details":{}, "correlation_id":"..."} }`. Не включать полный текст документа в ответы ошибок.

## Локальный доступ (реализовано в TASK_001)
- API слушает только `127.0.0.1`, порт выбирает ОС (`--port 0`). Оболочка передаёт capability-токен первой строкой stdin и получает `{"event":"ready","port":N}` в stdout; закрытие stdin — штатная остановка.
- Каждый запрос: `Authorization: Bearer <token>`. `Host` должен быть `127.0.0.1`/`localhost` (защита от DNS rebinding), `Origin` — из allowlist UI (Tauri origins; в dev — `http://127.0.0.1:5173`, `http://localhost:5173`).
- Каждый ответ содержит `X-Correlation-Id` (принимается от клиента, если `[A-Za-z0-9._-]{8,64}`, иначе генерируется UUID). Он же — `error.correlation_id`.
- JSON-тело ≤ 1 МБ. `/docs`, `/openapi.json` не публикуются; схема — `contracts/openapi/openapi.json` (генерация: `npm run api:openapi`, CI проверяет расхождение).
- `GET /health` → `{status, version, api_version, storage_mode, database}`.

## Workspace / Supplier
- `GET /workspaces?cursor=&limit=`; `POST /workspaces` `{name,locale,base_currency,country_code}`; `GET /workspaces/{id}`; `PATCH /workspaces/{id}` (`If-Match`).
- `GET /suppliers?workspace_id=&query=&include_archived=&cursor=&limit=` — `workspace_id` обязателен; поиск без учёта регистра (включая кириллицу), архивные скрыты по умолчанию.
- `POST /suppliers` `{workspace_id,name,country_code,default_currency,tax_id?,terms?}`; `GET /suppliers/{id}`.
- `PATCH /suppliers/{id}` для частичного изменения с `If-Match`; `tax_id`/`terms: null` очищают значение, `archived: true|false` — soft-delete/восстановление. Физического удаления нет (только будущий privacy deletion flow).
- Ответы с записью содержат `ETag: "<version>"`. `If-Match` принимает `"3"`, `W/"3"` или `3`. Нет заголовка → `428 PRECONDITION_REQUIRED`; версия устарела → `409 VERSION_CONFLICT` с `details.current_version`.
- Список: `{items, next_cursor}`; курсор непрозрачный (keyset по `created_at,id`), `limit` 1..200, по умолчанию 50. Неверный курсор → `400 INVALID_CURSOR`.

## Документы
- `POST /documents` multipart file + `supplier_id`; response `201 {id,sha256,status,duplicate_of?}`.
- `GET /documents?status=&supplier_id=&cursor=`; `GET /documents/{id}` метаданные/status.
- `POST /documents/{id}/processing-runs` `{profile?, cloud_ai_consent_id?}`; возвращает run id. Без consent cloud adapter не может быть запущен.
- `GET /processing-runs/{id}` статус, progress `{stage,completed,total}`, counts, error code.
- `POST /processing-runs/{id}/cancel`; `POST /processing-runs/{id}/retry` только для retryable статусов.
- `GET /documents/{id}/lines?run_id=&cursor=`; provenance включает source coordinates.
- `PATCH /extracted-fields/{id}` `{value,reason,expected_revision}`; optimistic concurrency.

### Форматы (pipeline `dip-0.3.0`)
Обрабатываются CSV, XLSX, XLS, DOCX (таблицы; без таблиц — абзацы), PDF с текстовым слоем. Логика DOCX/PDF перенесена из прототипа `pricepilot-web` (группировка слов в строки по координатам, колонки по заголовку). Для PDF значения получают `provenance.page` и `region` (0..1) вместо адреса ячейки; таблица продолжается между страницами, повтор заголовка на новой странице пропускается. Скан без текстового слоя (PDF-скан, JPG/PNG/TIFF/HEIC) — `OCR_RESOURCE_MISSING` / `UNSUPPORTED_FORMAT`, оригинал сохранён; распознавание — отдельный шаг. `source-rows`: строки содержат `page`, ячейки — `region`, `ref` = null для PDF.

### Реализовано в TASK_004 — рабочее место проверки
- `GET /documents/{id}/source-rows?sheet=&from=&to=` — фрагмент оригинала (только чтение, не более 200 строк): `{sheet, sheets[], first_row, last_row, total_rows, rows: [{index, cells: [{column, ref, text, formula}]}]}`. Оригинал перечитывается тем же адаптером, поэтому `ref` совпадает с `provenance.cell` строк; результат кэшируется по SHA-256. Неизвестный лист → `404`, неверный диапазон → `422`, формат без адаптера/нечитаемый оригинал → `409 INVALID_STATE`.
- UI выбирает колонку цены через решения по полям (`accept` одного `price_n`, `reject` остальных). Статус строки на сервере (`line_status`, `review_reasons`) отражает результат извлечения и не меняется; «строка проверена» вычисляется в UI из статусов полей. **Открытый вопрос (TASK_006):** нужно ли серверное решение на уровне строки/предложения («выбранная цена»).

### Реализовано в TASK_003 — импорт
- `POST /documents?workspace_id=&supplier_id=&source=file_picker|drag_drop|folder` — **тело запроса = байты файла** (`application/octet-stream`), имя — заголовок `X-File-Name` в percent-encoding UTF-8. *Отклонение от исходного «multipart»:* поток пишется сразу во внутренний карантин `incoming/` с подсчётом SHA-256 и лимитом размера, без системных временных файлов и без дополнительной multipart-зависимости.
- Ответ `201 {document, run, duplicate_of: null, error}`; повтор тех же байтов в workspace → `200` с `duplicate_of = id` существующего документа, без новой записи, запуска и строк (идемпотентность по SHA-256).
- `error` (`{code, message}`) заполнен, если файл **сохранён, но не обработан**: `UNSUPPORTED_FORMAT` (DOC; неизвестное содержимое; PDF/DOCX/XLS/изображения до появления адаптеров — запуск `failed`, `retryable=false`), `INVALID_CONTENT` (повреждён или содержимое не совпадает с расширением), `PASSWORD_REQUIRED`, `LIMIT_EXCEEDED` (ZIP-бомба), `NO_TABLE_FOUND`. Оригинал при этом хранится (неизменяемый, read-only).
- Ничего не сохраняется для: пустого файла (`422 INVALID_CONTENT`), файла больше лимита (`413 LIMIT_EXCEEDED`, по умолчанию 50 МБ), имени с путём (`422`), несуществующего workspace/поставщика (`404`).
- Обработка идёт в фоне: клиент опрашивает `GET /processing-runs/{id}`. Итог `completed` (строки без замечаний) или `review_required` (у строк есть `review_reasons`).
- `POST /documents/{id}/processing-runs` — запуск текущей версией pipeline (идемпотентно, `200` если уже есть); для формата без адаптера → `409 INVALID_STATE`.
- Строки (`GET /documents/{id}/lines`) получили `review_reasons[]`; `provenance` — `header` (заголовок исходного столбца) и `formula` (текст формулы, не вычисляется). Несколько столбцов цен: если у каждого распознан тип по заголовку (опт / розница / акция) — поля `price_wholesale` / `price_retail` / `price_promo` **без** пометки к проверке (это свойство прайса, какая цена применяется — решает сравнение, TASK_006); если типы не распознаны или повторяются (например, «Цена 1/Цена 2» со скана без заголовков) — `price_1…price_n` + `MULTIPLE_PRICE_COLUMNS`. Ни одна цена не выбирается автоматически. Правило введено в pipeline `dip-0.2.0`; документы, обработанные `dip-0.1.0`, сохраняют прежний результат до повторной обработки.

### Реализовано в TASK_002
- Документы регистрируются через `DocumentService.register` (дедупликация по `(workspace_id, sha256)`).
- `GET /documents?workspace_id=&status=&supplier_id=&cursor=&limit=` — `workspace_id` обязателен; `GET /documents/{id}` → `{…, status, latest_run_id, version}` + `ETag`. `storage_key` наружу не отдаётся.
- `GET /processing-runs/{id}` → `{status, attempt, progress{stage,completed,total}, error{code,retryable}, counts{lines,fields,lines_review_required}, started_at, finished_at}`. `POST …/cancel` — для незавершённых; `POST …/retry` — для `failed` с `retryable=true` и для отменённых пользователем (`cancelled`), иначе `409 INVALID_STATE`; запуск возвращается в `queued` с `attempt + 1`.
- `GET /documents/{id}/lines?run_id=&cursor=&limit=` — по умолчанию последний запуск; курсор по порядковому номеру строки; каждая строка содержит `source` и `fields[]` с `provenance` (`source_document_id, sheet, page, row, column, cell, region{x,y,width,height} в 0..1, localization: exact|approximate|none, extractor, extractor_version, pipeline_version, method`). Чужой `run_id` → `404`.
- `GET /extracted-fields/{id}`; `PATCH /extracted-fields/{id}` `{value, reason?, expected_revision}` меняет только `normalized_value` (`raw_value` неизменен), статус `corrected`, новая ревизия. `POST /extracted-fields/{id}/decision` `{decision: accept|reject, reason?, expected_revision}`; `accept` без нормализованного значения → 422. `GET /extracted-fields/{id}/revisions` — история. Устаревшая ревизия → `409 VERSION_CONFLICT` с `details.current_revision`.
- **Формат значений:** `money` → `{"amount": "1234.50", "currency": "RUB"}`, `quantity` → `{"value": "50", "unit": "kg"}`, остальные типы → `{"value": "…"}`. Числа — только строки `^-?\d{1,18}(\.\d{1,8})?$`; JSON-числа и локальные форматы (`1,234`, `1 234,5`) отклоняются с 422 — их разрешает человек при проверке.

## Каталог/сопоставление
- `GET /products?workspace_id=&query=&cursor=`; `POST /products` `{workspace_id, canonical_name, base_unit_code, brand?, manufacturer_code?, attributes?, identifiers?: [{scheme: gtin|supplier_sku|manufacturer_code|other, value, issuer?}]}`; `GET /products/{id}` (реализовано в TASK_002). GTIN проверяется по контрольной цифре и хранится как GTIN-14; `supplier_sku` требует `issuer`.
- `GET /units` — словарь единиц (код, размерность, UCUM, подписи ru/en).
- `GET /offers?product_id=&supplier_id=`.
- `POST /offers/{id}/match-decisions` `{product_id,decision:"accept|reject|defer",reason?,expected_revision}`.
- `POST /offers/{id}/suggest-matches` `{limit?}`; ответ кандидаты и объяснения, не факт.

## Сравнения
- `POST /comparisons` `{name,target_currency,as_of_date}`.
- `POST /comparisons/{id}/items` `{product_id,required_quantity,unit_code}`.
- `POST /comparisons/{id}/recalculate` `{fx_rate_set_id?,cost_policy_id}`.
- `GET /comparisons/{id}` включает только актуальные snapshots, предупреждения и происхождение.
- `POST /comparisons/{id}/exports` `{format:"csv|xlsx|pdf"}`; экспорт с async status.

## Общие требования
Cursor pagination, лимит страницы, фильтрация серверная. UUID формат. Время ISO 8601. Денежные суммы — decimal strings. API idempotency для создания импорта/экспорта. 401/403 для cloud identity; локальная сборка — ограниченный локальный principal. Rate/size limit. Версионировать несовместимые изменения через `/v2`.
