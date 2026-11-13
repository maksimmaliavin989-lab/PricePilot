# Ошибки и восстановление

## Категории
- `UNSUPPORTED_FORMAT`: формат не реализован (например DOC).
- `INVALID_CONTENT`: расширение/MIME не совпали, документ повреждён.
- `LIMIT_EXCEEDED`: размер/страницы/время/строки сверх лимита.
- `PASSWORD_REQUIRED`: зашифрованный документ.
- `EXTRACTION_LOW_CONFIDENCE`: результат требует проверки.
- `NO_TABLE_FOUND`: файл прочитан, но строки не обнаружены.
- `STORAGE_ERROR`, `DATABASE_ERROR`, `OCR_RESOURCE_MISSING`, `CANCELLED`, `INTERNAL_ERROR`.

### Общие коды API (реализованы в TASK_001)
| Код | HTTP | Когда |
|---|---|---|
| `VALIDATION_ERROR` | 422 | Поля не прошли схему или доменное правило; `details.fields[] = {loc,type,msg}` без введённых значений |
| `NOT_FOUND` | 404 | Нет маршрута или записи; `details.entity` |
| `VERSION_CONFLICT` | 409 | Устаревший `If-Match` (`details.current_version`) или `expected_revision` (`details.current_revision`) |
| `INVALID_STATE` | 409 | Переход состояния запрещён (`details.entity/current/target`): retry неповторяемой ошибки, повторная запись строк запуска, обработка неподдерживаемого документа |
| `PRECONDITION_REQUIRED` | 428 | `PATCH` без `If-Match` |
| `INVALID_IF_MATCH` / `INVALID_CURSOR` / `BAD_REQUEST` | 400 | Некорректный заголовок/курсор/запрос |
| `UNAUTHORIZED` | 401 | Нет или неверный локальный токен |
| `FORBIDDEN_ORIGIN` | 403 | `Origin` не из allowlist UI |
| `INVALID_HOST` | 400 | `Host` не loopback |
| `LIMIT_EXCEEDED` | 413 | Тело запроса больше лимита; `details.max_bytes` |
| `METHOD_NOT_ALLOWED` | 405 | Метод не поддерживается |
| `INTERRUPTED` | — (код запуска) | Обработка прервана перезапуском приложения; запуск автоматически повторяется (до 3 попыток) |
| `DATABASE_ERROR` / `INTERNAL_ERROR` | 500 | Сбой; детали только в локальном логе по correlation id |

## Поведение
Сообщение пользователю объясняет факт и действие. Технические детали идут в безопасный лог по correlation id. Не показывать stack trace, локальный полный путь или содержимое файла. Ошибка одного документа не влияет на остальные. Retry доступен только для временных ошибок; неподдерживаемый формат не предлагается бесконечно повторять. Неудачный parse сохраняет оригинал и состояние.

## Повторы
Backoff с ограничением; ручной retry создаёт event. Смена parser/pipeline версии создаёт новую run. Идемпотентные шаги защищают от повторных строк. Частичный импорт обозначается явно и требует пользовательского решения.
