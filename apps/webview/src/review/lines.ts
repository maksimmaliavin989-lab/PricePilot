import type { Field, Line } from "../api/client";
import { isPriceKey } from "./format";

export type LineFilter = "all" | "pending" | "attention" | "checked";
export type LineSort = "source" | "name";

/**
 * Review state of a line, derived from its fields (the server keeps the original extraction
 * flags; field decisions are what the user has actually checked):
 * - "attention": the extraction flagged it or a field was rejected without a replacement;
 * - "checked": every field has been accepted, corrected or rejected;
 * - "pending": nothing flagged, not yet checked.
 */
export type LineState = "attention" | "checked" | "pending";

function priceFields(line: Line): Field[] {
  return line.fields.filter((f) => isPriceKey(f.field_key));
}

/** Remarks the server still considers open (derived from the current fields). */
export function openIssues(line: Line): Line["issues"] {
  return line.issues.filter((i) => !i.resolved);
}

export function lineState(line: Line): LineState {
  if (openIssues(line).length > 0) return "attention";
  const decided = line.fields.every((f) => f.review_status !== "unreviewed");
  const prices = priceFields(line);
  const hasUsablePrice = prices.some(
    (f) => f.review_status === "accepted" || f.review_status === "corrected",
  );
  if (decided && (prices.length === 0 || hasUsablePrice)) return "checked";
  return decided ? "attention" : "pending";
}

export const LINE_STATE_LABELS: Record<LineState, string> = {
  attention: "Есть замечания",
  checked: "Проверено",
  pending: "Не проверено",
};

export function fieldOf(line: Line, key: string): Field | undefined {
  return line.fields.find((f) => f.field_key === key);
}

/** Filter, search and sort without losing which line is selected. */
export function visibleLines(
  lines: Line[],
  options: { filter: LineFilter; sort: LineSort; search: string },
): Line[] {
  const needle = options.search.trim().toLocaleLowerCase("ru");
  const filtered = lines.filter((line) => {
    const state = lineState(line);
    if (options.filter === "attention" && state !== "attention") return false;
    if (options.filter === "checked" && state !== "checked") return false;
    if (options.filter === "pending" && state !== "pending") return false;
    if (!needle) return true;
    return line.raw_text.toLocaleLowerCase("ru").includes(needle);
  });
  const sorted = [...filtered];
  if (options.sort === "name") {
    sorted.sort((a, b) =>
      (fieldOf(a, "name")?.raw_value ?? "").localeCompare(
        fieldOf(b, "name")?.raw_value ?? "",
        "ru",
      ),
    );
  } else {
    sorted.sort((a, b) => a.ordinal - b.ordinal);
  }
  return sorted;
}

/** Keyboard navigation: the next/previous visible line, staying inside the list. */
export function moveSelection(
  visible: Line[],
  selectedId: string | null,
  delta: 1 | -1,
): string | null {
  if (visible.length === 0) return selectedId;
  const index = visible.findIndex((l) => l.id === selectedId);
  if (index === -1) return visible[0]?.id ?? null;
  const next = Math.min(visible.length - 1, Math.max(0, index + delta));
  return visible[next]?.id ?? selectedId;
}

/** Replaces one field inside the lines after a successful review action. */
export function withField(lines: Line[], field: Field): Line[] {
  return lines.map((line) =>
    line.id === field.line_id
      ? { ...line, fields: line.fields.map((f) => (f.id === field.id ? field : f)) }
      : line,
  );
}

const DECIMAL = /^(?:0|[1-9]\d{0,17})(?:\.\d{1,8})?$/;

/** Accepts "1 234,50" / "1234.50" typed by a person and returns the canonical form or null.
 * A single separator followed by exactly three digits ("1,234") is ambiguous: rejected, the
 * user must write it unambiguously (the same rule as for imported data). */
export function canonicalDecimal(input: string): string | null {
  const trimmed = input.trim().replace(/[\s\u00a0]+/g, " ");
  // Spaces are allowed only as thousands separators in correct groups: "1 234 567,5".
  if (trimmed.includes(" ") && !/^\d{1,3}(?: \d{3})+(?:[.,]\d+)?$/.test(trimmed)) return null;
  const compact = trimmed.replace(/ /g, "");
  if (/^\d{1,3}[.,]\d{3}$/.test(compact)) return null;
  const normalized = compact.replace(",", ".");
  return DECIMAL.test(normalized) ? normalized : null;
}
