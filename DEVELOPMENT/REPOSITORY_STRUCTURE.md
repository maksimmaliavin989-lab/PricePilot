# Рекомендуемая структура репозитория

```text
pricepilot/
  apps/desktop/                 # Tauri 2 shell
  apps/webview/                 # React + TypeScript UI
  services/api/                  # FastAPI entrypoint, composition root
  packages/domain/               # Python доменные модели и правила
  packages/application/          # use cases, DTO, ports
  packages/infrastructure/       # SQL, files, OCR, parsers, adapters
  packages/dip_document_engine/  # общий DIP слой с отдельной версией
  migrations/                   # Alembic
  contracts/openapi/            # generated/checked API schema
  tests/fixtures/                # синтетические/обезличенные документы (price-lists/: manifest.json + README)
  docs/                          # этот пакет
  scripts/                       # dev/build/release helpers
```

Python backend может быть одним installable package с модулями внутри до появления нужды в workspace packaging. React вызывает API-клиент из typed OpenAPI схемы. Tauri permissions и capabilities находятся рядом с shell config. Не коммитить реальные документы, credentials, локальные DB или OCR модели без лицензии/явного решения.
