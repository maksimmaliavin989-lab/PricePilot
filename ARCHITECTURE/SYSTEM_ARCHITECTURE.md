# Системная архитектура

## Контекст
PricePilot — продуктовый модуль общей платформы DIP. Стартовая поставка — desktop-приложение Tauri 2. React/TypeScript UI общается с FastAPI через локальный HTTP API либо внутренний адаптер; локальный backend запускается/управляется оболочкой. Открытые порты ограничены loopback, а IPC и токены защищены. Позже тот же доменный API разворачивается как cloud service.

## Модули модульного монолита
1. Identity & Workspace — локальные пользователи/профили и рабочие пространства.
2. Supplier Registry — поставщики и условия.
3. Document Intake — файлы, hash, метаданные, карантин.
4. DIP Document Intelligence — OCR, layout, extraction, provenance.
5. Product Catalog — товары, атрибуты, единицы, упаковки.
6. Matching — кандидаты, оценка, подтверждения и отрицательные связи.
7. Procurement Comparison — наборы сравнения, требования и предложения.
8. Costing — компоненты landed cost, FX snapshot, версии формул.
9. Export & Audit — выгрузки, события, история.

Модули владеют своими таблицами и публикуют типизированные application services/events; прямые записи в чужие таблицы запрещены.

## Потоки данных
Import → quarantine/validation → immutable original → document job → format adapters/OCR → canonical extraction → user review → normalization → match suggestions → accepted offers → comparison/cost snapshot → export.

## Хранилища
- Локальный режим: SQLite (метаданные/доменные данные), файловое хранилище приложений для оригиналов и промежуточных артефактов.
- Cloud: PostgreSQL + объектное хранилище с envelope encryption.
- Абстракции storage/repository обязаны иметь одинаковую семантику. Внешний поиск/AI — адаптеры, выключенные по умолчанию.

## Надёжность
Очередь задач персистентна; задачи идемпотентны по document/version/step. Повтор ограничен. Отмена кооперативная. Ошибка конкретного документа не прерывает пакет. Изменение extraction pipeline создаёт новую версию результата, а не подменяет предыдущую.

## Развёртывание
Dev: UI, API и SQLite локально. Desktop: подписанный Tauri пакет с локальными ресурсами OCR. Cloud: контейнер API/worker, PostgreSQL, объектное хранилище, мониторинг и миграции. Cloud функции не требуются для базового MVP.
