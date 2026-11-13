# TASK 003 — Импорт файлов и extraction foundation

Начать с CSV/XLSX и immutable file store. Реализовать MIME sniff, limits, SHA-256 dedup, processing job, parser adapter interface, canonical lines/fields и provenance лист/строка/ячейка. Реализовать точные статусы и retry. Заложить интерфейсы PDF/DOCX/OCR, но не имитировать неподдержанную обработку.

**Приёмка:** корректный, пустой, повреждённый, oversized и MIME-spoofed fixture; частичная ошибка не останавливает batch; повторный hash не дублирует исходник/строки; импорт доступен без сети.
