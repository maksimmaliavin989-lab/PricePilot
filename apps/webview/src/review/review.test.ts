import { describe, expect, it } from "vitest";

import { ApiClient, ApiError } from "../api/client";
import { fieldLabel, formatDecimal, reasonLabel, valueText } from "./format";
import { canonicalDecimal, lineState, moveSelection, visibleLines, withField } from "./lines";
import { makeField, makeLine } from "./testData";
import { toItems, uploadAll, type UploadItem } from "./upload";

describe("formatDecimal", () => {
  it("groups digits without converting to a float", () => {
    expect(formatDecimal("1234.50")).toBe("1 234,50");
    expect(formatDecimal("999999999999999999.99999999")).toBe("999 999 999 999 999 999,99999999");
    expect(formatDecimal("540")).toBe("540");
    expect(formatDecimal("abc")).toBe("abc");
  });
});

describe("labels", () => {
  it("names price columns by their source header", () => {
    const f = makeField({ field_key: "price_2" });
    f.provenance.header = "Розница, руб.";
    expect(fieldLabel(f)).toBe("Цена 2 (Розница, руб.)");
    expect(fieldLabel(makeField({ field_key: "unit" }))).toBe("Единица");
    const wholesale = makeField({ field_key: "price_wholesale" });
    wholesale.provenance.header = "Опт, руб.";
    expect(fieldLabel(wholesale)).toBe("Цена: опт (Опт, руб.)");
    expect(reasonLabel("AMBIGUOUS_NUMBER")).toBe("Неоднозначное число");
    expect(reasonLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });

  it("shows an em dash when the value is not normalised", () => {
    expect(
      valueText(makeField({ field_key: "price", value_type: "money", normalized_value: null })),
    ).toBe("—");
    expect(
      valueText(
        makeField({
          field_key: "price",
          value_type: "money",
          normalized_value: { amount: "18.5", currency: "RUB" },
        }),
      ),
    ).toBe("18,5 RUB");
    const units = [
      { code: "pc", dimension: "count", ucum: "{piece}", label_ru: "шт", label_en: "pc" },
    ];
    expect(
      valueText(
        makeField({ field_key: "unit", value_type: "unit", normalized_value: { value: "pc" } }),
        units,
      ),
    ).toBe("шт");
  });
});

describe("typed prices", () => {
  it("is not 'attention' for опт/розница and is checked once either is accepted", () => {
    const opt = makeField({
      field_key: "price_wholesale",
      value_type: "money",
      normalized_value: { amount: "430", currency: "RUB" },
    });
    const retail = makeField({
      field_key: "price_retail",
      value_type: "money",
      normalized_value: { amount: "490", currency: "RUB" },
    });
    const line = makeLine("t", 1, "Штукатурка", { fields: [opt, retail] });
    expect(lineState(line)).toBe("pending");
    expect(
      lineState({
        ...line,
        fields: [
          { ...opt, review_status: "accepted" },
          { ...retail, review_status: "accepted" },
        ],
      }),
    ).toBe("checked");
  });
});

describe("lineState", () => {
  it("is 'attention' while extraction flags are open and 'checked' once a price is decided", () => {
    const p1 = makeField({
      field_key: "price_1",
      value_type: "money",
      normalized_value: { amount: "1", currency: "RUB" },
    });
    const p2 = makeField({
      field_key: "price_2",
      value_type: "money",
      normalized_value: { amount: "2", currency: "RUB" },
    });
    const name = makeField({ field_key: "name" });
    const line = makeLine("a", 1, "A", {
      reasons: ["MULTIPLE_PRICE_COLUMNS"],
      fields: [name, p1, p2],
    });
    expect(lineState(line)).toBe("attention");
    const decided = {
      ...line,
      issues: line.issues.map((i) => ({ ...i, resolved: true })),
      fields: [
        { ...name, review_status: "accepted" as const },
        { ...p1, review_status: "accepted" as const },
        { ...p2, review_status: "rejected" as const },
      ],
    };
    expect(lineState(decided)).toBe("checked");
    const allRejected = {
      ...decided,
      fields: decided.fields.map((f) => ({ ...f, review_status: "rejected" as const })),
    };
    expect(lineState(allRejected)).toBe("attention"); // no usable price left
    expect(lineState(makeLine("b", 2, "B"))).toBe("pending");
  });
});

describe("visibleLines and selection", () => {
  const lines = [
    makeLine("c", 3, "Цемент"),
    makeLine("a", 1, "Арматура", { reasons: ["UNKNOWN_UNIT"] }),
    makeLine("b", 2, "Брус"),
  ];

  it("sorts by source order or name and filters by state and text", () => {
    const opts = { filter: "all" as const, search: "" };
    expect(visibleLines(lines, { ...opts, sort: "source" }).map((l) => l.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(visibleLines(lines, { ...opts, sort: "name" }).map((l) => l.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(
      visibleLines(lines, { filter: "attention", sort: "source", search: "" }).map((l) => l.id),
    ).toEqual(["a"]);
    expect(
      visibleLines(lines, { filter: "all", sort: "source", search: "БРУС" }).map((l) => l.id),
    ).toEqual(["b"]);
  });

  it("moves within the visible list and starts at the first line", () => {
    const visible = visibleLines(lines, { filter: "all", sort: "source", search: "" });
    expect(moveSelection(visible, "a", 1)).toBe("b");
    expect(moveSelection(visible, "c", 1)).toBe("c");
    expect(moveSelection(visible, "a", -1)).toBe("a");
    expect(moveSelection(visible, "hidden", 1)).toBe("a");
  });

  it("replaces an updated field in place", () => {
    const field = lines[0]?.fields[1];
    expect(field).toBeDefined();
    if (!field) return;
    const updated = withField(lines, { ...field, revision: 2, review_status: "corrected" });
    expect(updated[0]?.fields[1]?.review_status).toBe("corrected");
    expect(updated[1]).toBe(lines[1]);
  });
});

describe("canonicalDecimal", () => {
  it("accepts unambiguous input and rejects the rest", () => {
    expect(canonicalDecimal("1 234,50")).toBe("1234.50");
    expect(canonicalDecimal("18,5")).toBe("18.5");
    expect(canonicalDecimal("1199.00")).toBe("1199.00");
    expect(canonicalDecimal("1,234")).toBeNull();
    expect(canonicalDecimal("4851 199,00")).toBeNull(); // malformed grouping, e.g. appended text
    expect(canonicalDecimal("1 234 567,5")).toBe("1234567.5");
    expect(canonicalDecimal("-5")).toBeNull();
    expect(canonicalDecimal("abc")).toBeNull();
  });
});

describe("uploadAll", () => {
  it("continues the batch after a rejected file and sends the encoded name", async () => {
    const sent: { name: string | null; url: string }[] = [];
    const client = new ApiClient({ baseUrl: "", token: null }, (input, init) => {
      const request = new Request(
        new URL(input instanceof Request ? input.url : input.toString(), "http://x"),
        init,
      );
      sent.push({ name: request.headers.get("x-file-name"), url: request.url });
      if (sent.length === 1) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "LIMIT_EXCEEDED",
                message: "Слишком большой",
                details: {},
                correlation_id: "c-1",
              },
            }),
            { status: 413 },
          ),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ document: {}, run: null, duplicate_of: null, error: null }), {
          status: 201,
        }),
      );
    });
    const files = [new File(["x"], "big.csv"), new File(["y"], "Прайс 1.xlsx")];
    const items = toItems(files);
    const seen: UploadItem[] = [];
    await uploadAll(client, files, items, { workspaceId: "w1", source: "drag_drop" }, (i) => {
      seen.push(i);
    });
    expect(seen.filter((i) => i.state !== "uploading").map((i) => i.state)).toEqual([
      "rejected",
      "done",
    ]);
    expect(seen.find((i) => i.state === "rejected")?.error).toEqual({
      code: "LIMIT_EXCEEDED",
      message: "Слишком большой",
      correlationId: "c-1",
    });
    expect(sent[1]?.name).toBe(encodeURIComponent("Прайс 1.xlsx"));
    expect(sent[1]?.url).toContain("source=drag_drop");
    expect(ApiError).toBeDefined();
  });
});
