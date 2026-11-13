# Модель базы данных

Все таблицы имеют UUID/ULID id, `created_at`, `updated_at`; время UTC. Денежные суммы — decimal + ISO 4217 currency; количественные значения — Decimal + unit code. Для SQLite Decimal сериализуется без потери точности; вычисления выполняются в доменном слое Decimal.

## Таблицы
- `workspaces`: name, locale, base_currency, country_code, storage_mode, settings_json/version.
- `suppliers`: workspace_id, name, country_code, tax_id (optional), default_currency, terms, archived_at.
- `source_documents`: supplier_id, original_name, detected_mime, extension, size_bytes, sha256, storage_key, import_source, status, retention_state.
- `processing_runs`: document_id, pipeline_version, config_hash, status, started_at, finished_at, error_code, diagnostics_ref.
- `extracted_lines`: run_id, ordinal, raw_text, sheet/page, row/region, line_status.
- `extracted_fields`: line_id, field_key, raw_value, normalized_value_json, confidence, provenance_json, review_status, revision.
- `products`: workspace_id, canonical_name, brand, manufacturer_code, attributes_json, base_unit_code, active.
- `product_identifiers`: product_id, scheme, value, normalized_value, issuer.
- `supplier_offers`: supplier_id, source_document_id, extracted_line_id, product_id nullable, supplier_sku, raw_name, pack_quantity, pack_unit, price_amount, currency, valid_from/to, minimum_order, lead_time_days, conditions_json, review_state.
- `match_suggestions`: offer_id, candidate_product_id, score, feature_explanations_json, model_version, state.
- `match_decisions`: offer_id, product_id, decision, actor_id, reason, supersedes_id.
- `comparisons`: workspace_id, name, target_currency, as_of_date, created_by.
- `comparison_items`: comparison_id, product_id, required_quantity, unit_code.
- `comparison_offers`: item_id, offer_id, accepted_match_decision_id, inclusion_state.
- `fx_rates`: base_currency, quote_currency, rate_decimal, effective_at, source, imported_at.
- `cost_snapshots`: comparison_offer_id, formula_version, input_snapshot_json, component_breakdown_json, total_amount, currency, unit_amount, warnings_json.
- `audit_events`: workspace_id, actor_id nullable, entity_type/id, action, before/after safe JSON, occurred_at, correlation_id.

## Реализовано (миграция `0001_baseline`, TASK_001)
- `workspaces`: id (UUID), name, locale, base_currency, country_code, storage_mode (`local`), settings_json, created_at, updated_at, version.
- `suppliers`: id, workspace_id → workspaces (RESTRICT), name, `name_key` (NFKC+casefold для поиска без учёта регистра на любом движке), country_code, default_currency, tax_id?, terms?, archived_at?, created_at, updated_at, version.
- `version` ≥ 1 — optimistic concurrency (`UPDATE … WHERE version = :expected`). Время хранится в UTC: `timestamptz` в PostgreSQL, naive UTC в SQLite с повторной маркировкой UTC при чтении.
- Индексы: `(created_at, id)` для keyset-пагинации; `suppliers(workspace_id, created_at, id)`, `suppliers(workspace_id, name_key)`. SQLite: `foreign_keys=ON`, WAL.
- Тест сверяет миграции с метаданными таблиц (нет дрейфа). Прогон на PostgreSQL — в TASK_002.

## Реализовано (миграция `0002_documents_catalog`, TASK_002)
- `source_documents` (+ `workspace_id`; `status` stored|processing|review_required|completed|failed|unsupported; unique `(workspace_id, sha256)`), `processing_runs` (unique `(document_id, pipeline_version, config_hash)` — ключ идемпотентности; `attempt`, `progress_*`, `error_code/error_retryable`), `extracted_lines` (unique `(run_id, ordinal)`; sheet/page/row_index/column_index/cell/region_json), `extracted_fields` (unique `(line_id, field_key)`; `value_type`, `confidence` 0..1 с CHECK), **`extracted_field_revisions`** (append-only история правок: предыдущее/новое значение, статус, причина, автор), `products`, `product_identifiers` (unique `(product_id, scheme, normalized_value)`, индекс `(scheme, normalized_value)`), `audit_events`.
- JSON-колонки: `JSON` в SQLite, `JSONB` в PostgreSQL; десятичные числа внутри JSON — строки, поэтому точность не зависит от движка. Статусы ограничены CHECK; FK — `RESTRICT` (физическое удаление только в privacy deletion flow).
- Миграция `0003_line_review_reasons`: `extracted_lines.review_reasons_json` — коды причин ручной проверки строки.
- Миграция `0004_offers_matching_costs` (TASK_006): `supplier_offers` (ссылка на документ и строку, `line_ordinal`, ключ поля цены, название/артикул/единица/цена как при создании, `price_confirmed`, `pack_json`; unique `(extracted_line_id, price_field_key)`), `match_decisions` (только добавление: `decision` accept/reject/revoke, `actor_id`, `reason`, `supersedes_id`, `candidate_json`, `override`), `cost_policies` (одна на workspace, `types_json`, `fingerprint`, версия), `fx_rates`, `tax_rate_sources` + `tax_rates`, `comparisons` (`target_currency`, `as_of_date`), `comparison_items` (количество в базовой единице), `comparison_offers` (`match_decision_id` — подтверждение, на основании которого предложение сравнивается; `terms_json`), `cost_snapshots` (неизменяемые: `snapshot_json` с копией входа, `input_hash`, `formula_version`, `policy_fingerprint`, итог/стоимость единицы или NULL, `complete`, `supersedes_id`). Денежные суммы и ставки — строки canonical decimal (не float), даты курсов и ставок — `DATE`. Таблица `match_suggestions` не создаётся: кандидаты считаются по запросу, показанный кандидат сохраняется в решении.
- Тесты миграций и репозиториев параметризованы по движку: SQLite всегда, PostgreSQL — при `PRICEPILOT_TEST_POSTGRES_URL` (в CI — сервис `postgres:17`).
- **PostgreSQL проверен в CI** (13.11.2026, [run 43397872185](https://github.com/maksimmaliavin989-lab/PricePilot/actions/runs/43397872185), коммит `b884e3f`): job `backend-postgres` на `postgres:17` прошёл полный `uv run pytest -q` — миграции 0001–0003 (чистая база, повтор, откат, обновление с данными, отсутствие дрейфа схемы) и тесты репозиториев/ограничений. Проверена только версия 17 и только в CI; другие версии PostgreSQL и облачное развёртывание не проверялись.

## Индексы и ограничения
Unique `(workspace_id, sha256)` для активной дедупликации (повторный импорт может ссылаться на существующий blob). Индексы по workspace/supplier/status, normalized identifier, product, comparison. Checks: nonnegative quantities/prices where domain allows, ISO-like code validation in service, source IDs foreign key. Не удалять audit references; soft-delete для доменных записей, каскадное физическое удаление только в privacy deletion flow.

## SQLite/PostgreSQL
Не использовать SQLite-only JSON semantics. Для concurrency/locking определить совместимость; миграции прогонять на обоих engines. Валидировать ограничения в домене и БД.
