import type { DocumentStatus, Field, ReviewStatus, RunStatus, Unit } from "../api/client";

/**
 * Formats a canonical decimal string ("1234.50") for display without converting it to a
 * JavaScript number: the value is never rounded or re-parsed through floating point.
 */
export function formatDecimal(value: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;
  const [, sign = "", integer = "", fraction] = match;
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return `${sign}${grouped}${fraction ? `,${fraction}` : ""}`;
}

const FIELD_LABELS: Record<string, string> = {
  name: "Наименование",
  sku: "Артикул",
  characteristics: "Характеристики",
  unit: "Единица",
  price: "Цена",
  section: "Раздел",
};

/** Typed price columns: several prices of one list (опт / розница / акция). */
export const PRICE_TYPE_LABELS: Record<string, string> = {
  price_wholesale: "Опт",
  price_retail: "Розница",
  price_promo: "Акция",
};

export function isPriceKey(key: string): boolean {
  return key === "price" || key.startsWith("price_");
}

/** Short price name for tables: "Опт", "Розница", "Цена 1"; "" for a single price. */
export function priceName(key: string): string {
  const typed = PRICE_TYPE_LABELS[key];
  if (typed) return typed;
  const n = /^price_(\d+)$/.exec(key);
  return n ? `Цена ${n[1] ?? ""}` : "";
}

export function fieldLabel(field: Pick<Field, "field_key" | "provenance">): string {
  const typed = PRICE_TYPE_LABELS[field.field_key];
  if (typed) {
    const header = field.provenance.header;
    return header ? `Цена: ${typed.toLowerCase()} (${header})` : `Цена: ${typed.toLowerCase()}`;
  }
  const priceN = /^price_(\d+)$/.exec(field.field_key);
  if (priceN) {
    const header = field.provenance.header;
    return header ? `Цена ${priceN[1] ?? ""} (${header})` : `Цена ${priceN[1] ?? ""}`;
  }
  return FIELD_LABELS[field.field_key] ?? field.field_key;
}

/** Human-readable normalized value; "—" when the value could not be normalised. */
export function valueText(
  field: Pick<Field, "value_type" | "normalized_value">,
  units: readonly Unit[] = [],
): string {
  const value = field.normalized_value;
  if (!value) return "—";
  const unitLabel = (code: string | undefined) =>
    units.find((u) => u.code === code)?.label_ru ?? code ?? "";
  if (field.value_type === "unit") return unitLabel(value.value);
  if (field.value_type === "money") {
    return `${formatDecimal(value.amount ?? "")} ${value.currency ?? ""}`.trim();
  }
  if (field.value_type === "quantity") {
    return `${formatDecimal(value.value ?? "")} ${unitLabel(value.unit)}`.trim();
  }
  if (field.value_type === "decimal") return formatDecimal(value.value ?? "");
  return value.value ?? "—";
}

export const REASON_LABELS: Record<string, string> = {
  MULTIPLE_PRICE_COLUMNS: "Несколько цен — выберите нужную",
  AMBIGUOUS_NUMBER: "Неоднозначное число",
  INVALID_NUMBER: "Число не распознано",
  MISSING_PRICE: "Нет цены",
  MISSING_UNIT: "Нет единицы",
  UNKNOWN_UNIT: "Неизвестная единица",
  ASSUMED_CURRENCY: "Валюта не указана — предположена",
  COLUMNS_INFERRED: "Заголовок таблицы не прочитан — столбцы определены по содержимому",
  UNIT_CORRECTED: "Единица распознана с исправлением — подтвердите",
  UNIT_AMBIGUOUS: "«м» со скана: м, м² или м³? — подтвердите",
};

export function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? code;
}

export const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  unreviewed: "Не проверено",
  accepted: "Подтверждено",
  corrected: "Исправлено",
  rejected: "Отклонено",
};

export const DOCUMENT_STATUS_LABELS: Record<DocumentStatus, string> = {
  stored: "Сохранён, не обработан",
  processing: "Обрабатывается",
  review_required: "Требует проверки",
  completed: "Извлечено",
  failed: "Ошибка",
  unsupported: "Формат не поддерживается",
};

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  queued: "В очереди",
  validating: "Проверка файла",
  parsing: "Чтение",
  ocr: "Распознавание",
  extracting: "Извлечение строк",
  validating_result: "Проверка результата",
  review_required: "Требует проверки",
  completed: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
  retry_wait: "Ожидает повтора",
};

export const ERROR_LABELS: Record<string, string> = {
  UNSUPPORTED_FORMAT: "Формат не поддерживается",
  INVALID_CONTENT: "Файл повреждён или не соответствует расширению",
  LIMIT_EXCEEDED: "Превышен лимит",
  PASSWORD_REQUIRED: "Файл защищён паролем",
  NO_TABLE_FOUND: "Таблица товаров не найдена",
  STORAGE_ERROR: "Ошибка хранилища",
  INTERRUPTED: "Прервано перезапуском",
  CANCELLED: "Отменено",
  INTERNAL_ERROR: "Внутренняя ошибка",
};

export function errorLabel(code: string): string {
  return ERROR_LABELS[code] ?? code;
}

export const ACTIVE_RUN: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "queued",
  "validating",
  "parsing",
  "ocr",
  "extracting",
  "validating_result",
  "retry_wait",
]);
