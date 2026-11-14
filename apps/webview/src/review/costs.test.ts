import { describe, expect, it } from "vitest";

import type { CostPolicy } from "../api/client";
import { formatDecimal } from "./format";
import { isPrimaryRow, draftFromTerms, formatMoney, rankKey, termsFromDraft } from "./costs";

const policy: CostPolicy = {
  name: "Базовая",
  fingerprint: "x",
  version: 0,
  types: [
    {
      code: "delivery",
      label: "Доставка",
      category: "charge",
      basis: "per_order",
      base: "goods_net",
      required: true,
      default_value: null,
      default_currency: null,
    },
    {
      code: "vat",
      label: "НДС",
      category: "tax",
      basis: "percent",
      base: "subtotal",
      required: false,
      default_value: null,
      default_currency: null,
    },
  ],
};

describe("cost helpers", () => {
  it("shows a missing amount as unknown, never as zero", () => {
    expect(formatMoney(null)).toBe("неизвестно");
    expect(formatMoney({ amount: "12300.00", currency: "RUB" })).toBe(
      `${formatDecimal("12300.00")} RUB`,
    );
  });

  it("omits components that are not stated and keeps unknown ones explicit", () => {
    const draft = draftFromTerms(policy, null, "RUB");
    expect(draft.rows.map((r) => r.status)).toEqual(["unset", "unset"]);
    const unknown = {
      ...draft,
      rows: draft.rows.map((r, i) => (i === 0 ? { ...r, status: "unknown" as const } : r)),
    };
    expect(termsFromDraft(unknown)).toEqual({
      components: [{ code: "delivery", status: "unknown", source: null }],
      minimum_order: null,
      order_multiple: null,
      price_includes_vat: null,
    });
  });

  it("requires a value, a currency for amounts and a source for taxes", () => {
    const draft = draftFromTerms(policy, null, "RUB");
    const set = (patch: Record<number, object>) => ({
      ...draft,
      rows: draft.rows.map((r, i) => ({ ...r, ...(patch[i] ?? {}) })),
    });
    expect(termsFromDraft(set({ 0: { status: "known", value: "" } }))).toMatch(/укажите число/);
    expect(termsFromDraft(set({ 0: { status: "known", value: "1500", currency: "" } }))).toMatch(
      /валюту/,
    );
    expect(termsFromDraft(set({ 1: { status: "known", value: "20" } }))).toMatch(/откуда ставка/);
    const ok = termsFromDraft(
      set({
        0: { status: "known", value: "1 500".replace(" ", ""), currency: "rub" },
        1: { status: "known", value: "20", source: "договор" },
      }),
    );
    expect(ok).toEqual({
      components: [
        { code: "delivery", status: "known", value: "1500", currency: "RUB", source: null },
        { code: "vat", status: "known", value: "20", currency: null, source: "договор" },
      ],
      minimum_order: null,
      order_multiple: null,
      price_includes_vat: null,
    });
  });

  it("shows required and unknown components first, optional ones on demand", () => {
    const [delivery, vat] = draftFromTerms(policy, null, "RUB").rows;
    if (!delivery || !vat) throw new Error("rows");
    expect(isPrimaryRow(delivery, new Set())).toBe(true); // required
    expect(isPrimaryRow(vat, new Set())).toBe(false); // optional, not stated
    expect(isPrimaryRow(vat, new Set(["vat"]))).toBe(true); // unknown in the last result
    expect(isPrimaryRow({ ...vat, status: "not_applicable" }, new Set())).toBe(true);
  });

  it("ranks complete results first, unknown totals last", () => {
    expect(rankKey(null)[0]).toBe(2);
    expect(rankKey({ complete: false, unit_cost: null })[0]).toBe(2);
    expect(rankKey({ complete: true, unit_cost: { amount: "12.3" } })).toEqual([0, 12.3]);
  });
});
