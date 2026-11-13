import type { ComponentType, CostComponentIn, CostPolicy, OfferTerms } from "../api/client";
import { formatDecimal } from "./format";

export const UNKNOWN = "неизвестно";

/** Money for display; a missing amount is shown as unknown, never as zero. */
export function formatMoney(
  value: { amount: string; currency: string } | null | undefined,
): string {
  if (!value) return UNKNOWN;
  return `${formatDecimal(value.amount)} ${value.currency}`;
}

export const CATEGORY_LABELS: Record<ComponentType["category"], string> = {
  discount: "Скидка",
  charge: "Расход",
  tax: "Налог",
  duty: "Пошлина",
};

export const BASIS_LABELS: Record<ComponentType["basis"], string> = {
  per_order: "сумма за заказ",
  per_unit: "сумма за единицу цены",
  percent: "процент",
};

export const BASE_LABELS: Record<NonNullable<ComponentType["base"]>, string> = {
  goods_gross: "от товара до скидок",
  goods_net: "от товара после скидок",
  subtotal: "от суммы выше",
};

export const FEATURE_LABELS: Record<string, string> = {
  GTIN_EQUAL: "GTIN совпадает",
  GTIN_CONFLICT: "GTIN различается",
  SUPPLIER_SKU_EQUAL: "Артикул поставщика совпадает",
  SKU_OF_OTHER_SUPPLIER: "Артикул другого поставщика",
  MANUFACTURER_CODE_EQUAL: "Код производителя совпадает",
  NAME_TOKENS: "Слова названия",
  ATTRIBUTE_EQUAL: "Атрибут совпадает",
  ATTRIBUTE_CONFLICT: "Атрибуты различаются",
  PACK_CONFLICT: "Другая упаковка",
  PACK_DIFFERS_NORMALIZABLE: "Другая фасовка (приводится)",
  UNIT_INCOMPATIBLE: "Единицы несовместимы",
};

export function featureLabel(code: string): string {
  return FEATURE_LABELS[code] ?? code;
}

export const ORIGIN_LABELS: Record<string, string> = {
  manual: "введено",
  policy_default: "по умолчанию политики",
  verified_source: "проверенный источник",
};

/** One editable row per policy component type. "unset" = not stated in this comparison. */
export type RowStatus = "unset" | "known" | "unknown" | "not_applicable";

export interface TermsRow {
  type: ComponentType;
  status: RowStatus;
  value: string;
  currency: string;
  source: string;
}

export interface TermsDraft {
  rows: TermsRow[];
  minimumOrder: string;
  orderMultiple: string;
  priceIncludesVat: "unknown" | "yes" | "no";
}

export function draftFromTerms(
  policy: CostPolicy,
  terms: OfferTerms | null,
  currency: string,
): TermsDraft {
  const entered = new Map((terms?.components ?? []).map((c) => [c.code, c]));
  return {
    rows: policy.types.map((type) => {
      const c = entered.get(type.code);
      return {
        type,
        status: c ? c.status : "unset",
        value: c?.value ?? "",
        currency: c?.currency ?? (type.basis === "percent" ? "" : currency),
        source: c?.source ?? "",
      };
    }),
    minimumOrder: terms?.minimum_order ?? "",
    orderMultiple: terms?.order_multiple ?? "",
    priceIncludesVat:
      terms?.price_includes_vat === true
        ? "yes"
        : terms?.price_includes_vat === false
          ? "no"
          : "unknown",
  };
}

const DECIMAL = /^\d+(?:[.,]\d+)?$/;

function decimal(value: string): string {
  return value.trim().replace(",", ".");
}

/** Builds the request body; returns an error message instead when the draft is invalid. */
export function termsFromDraft(draft: TermsDraft): OfferTerms | string {
  const components: CostComponentIn[] = [];
  for (const row of draft.rows) {
    if (row.status === "unset") continue;
    if (row.status !== "known") {
      components.push({
        code: row.type.code,
        status: row.status,
        source: row.source.trim() || null,
      });
      continue;
    }
    if (!DECIMAL.test(row.value.trim())) {
      return `«${row.type.label}»: укажите число или отметьте «неизвестно».`;
    }
    const taxLike = row.type.category === "tax" || row.type.category === "duty";
    if (taxLike && !row.source.trim()) {
      return `«${row.type.label}»: укажите, откуда ставка (договор, документ).`;
    }
    const percent = row.type.basis === "percent";
    if (!percent && !/^[A-Z]{3}$/.test(row.currency.trim().toUpperCase())) {
      return `«${row.type.label}»: укажите валюту суммы.`;
    }
    components.push({
      code: row.type.code,
      status: "known",
      value: decimal(row.value),
      currency: percent ? null : row.currency.trim().toUpperCase(),
      source: row.source.trim() || null,
    });
  }
  for (const [label, value] of [
    ["Минимальный заказ", draft.minimumOrder],
    ["Кратность", draft.orderMultiple],
  ] as const) {
    if (value.trim() && !DECIMAL.test(value.trim())) return `${label}: укажите число.`;
  }
  return {
    components,
    minimum_order: draft.minimumOrder.trim() ? decimal(draft.minimumOrder) : null,
    order_multiple: draft.orderMultiple.trim() ? decimal(draft.orderMultiple) : null,
    price_includes_vat:
      draft.priceIncludesVat === "unknown" ? null : draft.priceIncludesVat === "yes",
  };
}

/** Comparable cost per base unit for ranking: complete results first, then by unit cost. */
export function rankKey(
  snapshot: {
    complete: boolean;
    unit_cost: { amount: string } | null;
  } | null,
): [number, number] {
  if (!snapshot?.unit_cost) return [2, 0];
  // Display ordering only; amounts themselves are never computed in the UI.
  return [snapshot.complete ? 0 : 1, Number(snapshot.unit_cost.amount)];
}
