import { describe, expect, it } from "vitest";

import type { Offer } from "../api/client";
import { EMPTY_PROGRESS, currentStep, groupOffers, steps } from "./progress";

function offer(id: string, line: string, key: string, product: string | null, ordinal = 1): Offer {
  return {
    id,
    extracted_line_id: line,
    line_ordinal: ordinal,
    price_field_key: key,
    name: `Товар ${line}`,
    matched_product_id: product,
  } as unknown as Offer;
}

describe("groupOffers", () => {
  it("shows one row per price-list line and keeps every original price", () => {
    const groups = groupOffers([
      offer("a", "l2", "price_retail", "p1", 2),
      offer("b", "l2", "price_wholesale", "p1", 2),
      offer("c", "l1", "price", null, 1),
    ]);
    expect(groups.map((g) => [g.lineId, g.offers.length, g.matched, g.partial])).toEqual([
      ["l1", 1, null, false],
      ["l2", 2, "p1", false],
    ]);
    expect(groups[1]?.offers.map((o) => o.price_field_key)).toEqual([
      "price_retail",
      "price_wholesale",
    ]);
  });

  it("marks a line matched only partly", () => {
    const [group] = groupOffers([
      offer("a", "l1", "price_retail", "p1"),
      offer("b", "l1", "price_wholesale", null),
    ]);
    expect(group?.matched).toBeNull();
    expect(group?.partial).toBe(true);
  });
});

describe("step states", () => {
  it("does not mark a step done while it is only started", () => {
    const p = {
      ...EMPTY_PROGRESS,
      documents: 1,
      lines: 41,
      checkedLines: 41,
      offerLines: 41,
      matchedLines: 1,
      comparisons: 1,
      calculated: 1,
      complete: 0,
    };
    expect(steps(p).map((s) => s.state)).toEqual(["done", "done", "active", "active"]);
    expect(steps(p)[2]?.hint).toBe("сопоставлено 1 из 41 строк");
    expect(steps(p)[3]?.hint).toBe("есть неизвестные условия");
    expect(steps(EMPTY_PROGRESS).map((s) => s.state)).toEqual(["todo", "todo", "todo", "todo"]);
  });
});

describe("path steps", () => {
  it("points to the first step that is not done", () => {
    expect(currentStep(EMPTY_PROGRESS)).toBe("import");
    const imported = { ...EMPTY_PROGRESS, documents: 1, lines: 41 };
    expect(currentStep(imported)).toBe("review");
    expect(steps(imported)[1]?.hint).toBe("проверено 0 из 41");
    const reviewed = { ...imported, checkedLines: 41, offerLines: 41 };
    expect(currentStep(reviewed)).toBe("match");
    // Matching 2 of 41 lines stays "in progress", but the user may go on to compare.
    const partly = { ...reviewed, matchedLines: 2 };
    expect(steps(partly)[2]?.state).toBe("active");
    expect(currentStep(partly)).toBe("compare");
    expect(currentStep({ ...reviewed, matchedLines: 41, comparisons: 1 })).toBe("compare");
  });
});
