// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ApiClient, type CostSnapshot, type Offer } from "../api/client";
import { Breakdown } from "./ComparisonView";
import { groupOffers } from "../journey/progress";
import { LinePanel } from "./MatchingView";

afterEach(() => {
  cleanup();
});

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

const SNAPSHOT = {
  id: "11111111-2222-3333-4444-555555555555",
  created_at: "2026-11-13T12:00:00Z",
  formula_version: "landed-2026.2",
  policy_fingerprint: "f",
  input_hash: "h",
  ordered: { value: "20", unit: "pc" },
  purchased: { value: "1000", unit: "kg" },
  components: [
    {
      code: "goods",
      label: "Товар",
      category: "goods",
      status: "known",
      amount: "10800.00",
      currency: "RUB",
      detail: "540 RUB × 20 pc",
      origin: null,
      original: null,
      fx: null,
      source: null,
      verified_rate: null,
    },
    {
      code: "delivery",
      label: "Доставка",
      category: "charge",
      status: "unknown",
      amount: null,
      currency: "RUB",
      detail: "неизвестно: обязательный компонент не указан",
      origin: "manual",
      original: null,
      fx: null,
      source: null,
      verified_rate: null,
    },
  ],
  total: null,
  unit_cost: null,
  known_subtotal: { amount: "10800.00", currency: "RUB" },
  unknown_components: ["delivery"],
  warnings: [
    { code: "UNKNOWN_COMPONENT", message: "«Доставка»: значение неизвестно — итог не рассчитан." },
  ],
  complete: false,
  supersedes_id: null,
} as CostSnapshot;

describe("Breakdown", () => {
  it("shows an unknown component as unknown and explains why there is no total", () => {
    render(<Breakdown snapshot={SNAPSHOT} />);
    const rows = screen.getAllByRole("row");
    expect(rows[2]?.textContent).toContain("Доставка");
    expect(rows[2]?.textContent).toContain("неизвестно");
    expect(rows[2]?.textContent).not.toContain("0,00");
    expect(screen.getByLabelText("Предупреждения").textContent).toContain("итог не рассчитан");
  });
});

const OFFER = {
  id: "o1",
  extracted_line_id: "l1",
  name: "Цемент М400 Д20, мешок 50 кг",
  unit: "pc",
  price: { amount: "495", currency: "RUB" },
  price_field_key: "price",
  line_ordinal: 6,
  price_confirmed: false,
  pack: null,
  matched_product_id: null,
  decisions: [],
  version: 1,
  supplier_sku: null,
  supplier_id: null,
} as unknown as Offer;

function fail(): never {
  throw new Error("no group");
}

describe("LinePanel", () => {
  it("requires a reason before accepting a candidate the matcher blocked", async () => {
    const posted: unknown[] = [];
    const client = new ApiClient({ baseUrl: "", token: null }, (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input.toString(), "http://x");
      if (url.pathname.endsWith("/candidates")) {
        return json({
          offer: OFFER,
          auto_link_enabled: false,
          candidates: [
            {
              product_id: "p1",
              canonical_name: "Цемент М500 Д0, мешок 50 кг",
              base_unit_code: "pc",
              score: "0.00",
              blocked: true,
              matcher_version: "match-2026.1",
              features: [
                {
                  code: "ATTRIBUTE_CONFLICT",
                  weight: "0",
                  detail: "разная марка: м400 ≠ м500",
                  blocking: true,
                },
              ],
            },
          ],
        });
      }
      if (init?.method === "POST") {
        posted.push(JSON.parse(init.body as string));
        return json({ ...OFFER, matched_product_id: "p1" }, 201);
      }
      return json({}, 404);
    });
    render(
      <LinePanel
        client={client}
        group={groupOffers([OFFER])[0] ?? fail()}
        units={[]}
        productName={() => "товар"}
        onChanged={() => undefined}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText(/разная марка/)).toBeTruthy();
    });
    const accept = screen.getByRole("button", { name: "Подтвердить" });
    expect((accept as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Причина решения/), {
      target: { value: "одна позиция в договоре" },
    });
    expect((accept as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(accept);
    await waitFor(() => {
      expect(posted).toEqual([
        { decision: "accept", product_id: "p1", reason: "одна позиция в договоре" },
      ]);
    });
  });
});
