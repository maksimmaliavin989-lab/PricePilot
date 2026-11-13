# Дорожная карта

## Phase 0 — Product & engineering baseline
Уточнить сегмент/тип прайсов, согласовать словарь единиц, политику валют и demo dataset; ADR по OCR и упаковке. Выход: Sprint 01 критерии утверждены.

## Phase 1 — MVP local (Sprints 01–03)
Foundation + CSV/XLSX/DOCX/PDF digital import; OCR PDF/images; review/provenance; supplier registry; canonical product catalog; простые explainable match suggestions; manual compare; базовые стоимость/экспорт с пользовательскими параметрами.

## Phase 2 — Pilot readiness
Улучшить таблицы сложных форматов, сопоставление упаковок и quality dashboard; миграции и backups; аудит исправлений; usability пилот с реальными обезличенными файлами.

## Phase 3 — Cloud beta
PostgreSQL/object storage, identity/tenant boundary, синхронизация и конфликты, opt-in cloud AI adapters, deletion/retention, deployment/security review.

## Phase 4 — Internationalization & scale
Локали/валюты/единицы, дополнительные parser languages, multi-currency policies, jurisdiction-specific tax/cost packs после юридической проверки, командные workflow.

## Exit gates
Не начинать cloud sync до согласованной модели конфликтов и приватности; не включать автоматические tax/customs rates до утверждения владельца/источника; не объявлять matching production-grade до benchmark набора и оценки ошибок. Оценки сроков уточнять по скорости пилотной команды.
