# Канонические модели данных

## SourceDocument
Оригинал и его описание. `id`, `workspace_id`, `supplier_id?`, имя, detected MIME, размер, SHA-256, storage ref, импортирован в, lifecycle status. Бинарник immutable; повторная загрузка того же hash — dedup reference.

## ExtractedField
`field_key`, `raw_value`, `normalized_value`, тип, confidence 0..1 (может быть null), provenance, extractor/version, review status `unreviewed|accepted|corrected|rejected`, revision. `normalized_value` никогда не заменяет `raw_value`.

## Product
Каноническая товарная единица с брендом, производителем, атрибутами, идентификаторами и базовой единицей. Синонимы/локализованные имена расширяются отдельными записями.

## SupplierOffer
Предложение поставщика, привязанное к исходной строке. Цена относится к указанной упаковке и валюте; условия имеют validity interval. Поля могут быть частично неизвестны; UI показывает неполноту.

## Quantity & unit
**Реализовано (TASK_002, предварительно):** коды `pc, pack, set, sheet, bag, roll, m, m2, m3, l, kg, t` с размерностью и UCUM (`{piece}`, `m2`, `L`…); полный словарь утверждается в Phase 0. Преобразований между единицами пока нет.

Quantity хранится decimal; единицы кодируются контролируемым словарём (UCUM-совместимый где применимо) и локализованной подписью. Преобразования имеют источник/версию и запрещены между несовместимыми размерностями.

## Match
Suggestion содержит оценку и признаки; decision — отдельная запись пользователя/правила. Переопределения создают новую decision с supersedes; исходная связь предложения сохраняется.

## Cost snapshot
Immutable input snapshot + policy/formula version + компоненты + результат + warnings. Пересчёт создаёт новый snapshot; курс FX и его дата/источник копируются внутрь расчёта.

## Валюты и локали
Использовать ISO 4217 codes, хранить locale tag (BCP 47), country code ISO 3166-1 alpha-2. Формат отображения — локальный, значение — каноническое. Не применять числовую локаль при парсинге без явной эвристики и проверки неоднозначности.
