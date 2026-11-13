/* Test-only builders for API-shaped review data. */
import type { Field, Line } from "../api/client";

let seq = 0;

export function makeField(overrides: Partial<Field> & Pick<Field, "field_key">): Field {
  seq += 1;
  return {
    id: `f${String(seq)}`,
    line_id: "l1",
    value_type: "text",
    raw_value: "x",
    normalized_value: { value: "x" },
    confidence: null,
    provenance: {
      source_document_id: "d1",
      sheet: "Прайс-лист",
      page: null,
      row: 7,
      column: 2,
      cell: "B7",
      region: null,
      localization: "exact",
      extractor: "xlsx",
      extractor_version: "1.0.0",
      pipeline_version: "dip-0.1.0",
      method: "table_cell",
      header: null,
      formula: null,
    },
    review_status: "unreviewed",
    revision: 1,
    reviewed_by: null,
    reviewed_at: null,
    ...overrides,
  };
}

export function makeLine(
  id: string,
  ordinal: number,
  name: string,
  options: { reasons?: string[]; fields?: Field[] } = {},
): Line {
  return {
    id,
    run_id: "r1",
    ordinal,
    raw_text: `${name} | шт. | 100`,
    source: {
      sheet: "Прайс-лист",
      page: null,
      row: ordinal + 6,
      column: null,
      cell: null,
      region: null,
      localization: "approximate",
    },
    status: options.reasons?.length ? "review_required" : "pending",
    review_reasons: options.reasons ?? [],
    fields: options.fields ?? [
      makeField({
        field_key: "name",
        line_id: id,
        raw_value: name,
        normalized_value: { value: name },
      }),
      makeField({
        field_key: "price",
        line_id: id,
        value_type: "money",
        raw_value: "100",
        normalized_value: { amount: "100", currency: "RUB" },
      }),
    ],
  };
}
