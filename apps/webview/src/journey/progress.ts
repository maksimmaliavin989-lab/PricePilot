import type { Line, Offer } from "../api/client";
import { lineState } from "../review/lines";

/** The main path of the app: price list → reviewed lines → matched catalog → comparison. */
export type StepId = "import" | "review" | "match" | "compare";

export interface StepView {
  id: StepId;
  title: string;
  hint: string;
  done: boolean;
}

export interface Progress {
  documents: number; // processed documents (lines extracted)
  processing: number;
  lines: number;
  checkedLines: number;
  attentionLines: number;
  offerLines: number; // price-list lines that have offers
  matchedLines: number;
  comparisons: number;
  calculated: number; // comparisons with at least one calculation
}

export const EMPTY_PROGRESS: Progress = {
  documents: 0,
  processing: 0,
  lines: 0,
  checkedLines: 0,
  attentionLines: 0,
  offerLines: 0,
  matchedLines: 0,
  comparisons: 0,
  calculated: 0,
};

export function reviewCounts(lines: readonly Line[]): {
  checked: number;
  attention: number;
  pending: number;
} {
  let checked = 0;
  let attention = 0;
  let pending = 0;
  for (const line of lines) {
    const state = lineState(line);
    if (state === "checked") checked += 1;
    else if (state === "attention") attention += 1;
    else pending += 1;
  }
  return { checked, attention, pending };
}

/** Offers of one price-list line (опт и розница — одна строка, две исходные цены). */
export interface OfferGroup {
  lineId: string;
  ordinal: number;
  name: string;
  offers: Offer[];
  /** The product all offers of the line are matched to (null if none or if they differ). */
  matched: string | null;
  /** Some prices of the line are matched and some are not, or to different products. */
  partial: boolean;
}

export function groupOffers(offers: readonly Offer[]): OfferGroup[] {
  const groups = new Map<string, Offer[]>();
  for (const offer of offers) {
    const list = groups.get(offer.extracted_line_id) ?? [];
    list.push(offer);
    groups.set(offer.extracted_line_id, list);
  }
  return [...groups.entries()]
    .map(([lineId, list]) => {
      const sorted = [...list].sort((a, b) => a.price_field_key.localeCompare(b.price_field_key));
      const first = sorted[0];
      const products = new Set(sorted.map((o) => o.matched_product_id ?? ""));
      const only = [...products][0] ?? "";
      return {
        lineId,
        ordinal: first?.line_ordinal ?? 0,
        name: first?.name ?? "",
        offers: sorted,
        matched: products.size > 1 ? null : only || null,
        partial: products.size > 1,
      };
    })
    .sort((a, b) => a.ordinal - b.ordinal);
}

export function steps(p: Progress): StepView[] {
  return [
    {
      id: "import",
      title: "Загрузить прайс",
      hint: p.documents ? `обработано документов: ${String(p.documents)}` : "файл поставщика",
      done: p.documents > 0,
    },
    {
      id: "review",
      title: "Проверить строки",
      hint: p.lines
        ? `проверено ${String(p.checkedLines)} из ${String(p.lines)}`
        : "цены и единицы из файла",
      done: p.lines > 0 && p.checkedLines === p.lines,
    },
    {
      id: "match",
      title: "Сопоставить с каталогом",
      hint: p.offerLines
        ? `сопоставлено ${String(p.matchedLines)} из ${String(p.offerLines)} строк`
        : "строки прайса → товары",
      done: p.matchedLines > 0,
    },
    {
      id: "compare",
      title: "Сравнить предложения",
      hint: p.comparisons
        ? `сравнений: ${String(p.comparisons)}, рассчитано: ${String(p.calculated)}`
        : "полная стоимость",
      done: p.calculated > 0,
    },
  ];
}

/** The first step that is not done: where the user should go next. */
export function currentStep(p: Progress): StepId {
  return steps(p).find((s) => !s.done)?.id ?? "compare";
}

const READY = new Set(["completed", "review_required"]);

/** Loads the path progress of a workspace (local API; a few requests per document). */
export async function loadProgress(
  client: import("../api/client").ApiClient,
  workspaceId: string,
): Promise<Progress> {
  const [docs, offers, comparisons] = await Promise.all([
    client.listDocuments(workspaceId),
    client.listAllOffers(workspaceId),
    client.listComparisons(workspaceId),
  ]);
  const ready = docs.items.filter((d) => READY.has(d.status)).slice(-10);
  const lineLists = await Promise.all(ready.map((d) => client.listAllLines(d.id)));
  const counts = reviewCounts(lineLists.flat());
  const groups = groupOffers(offers);
  const details = await Promise.all(
    comparisons.items.slice(-20).map((c) => client.getComparison(c.id)),
  );
  return {
    documents: ready.length,
    processing: docs.items.filter((d) => d.status === "processing").length,
    lines: counts.checked + counts.attention + counts.pending,
    checkedLines: counts.checked,
    attentionLines: counts.attention,
    offerLines: groups.length,
    matchedLines: groups.filter((g) => g.matched).length,
    comparisons: comparisons.items.length,
    calculated: details.filter((c) => c.items.some((i) => i.offers.some((o) => o.snapshot))).length,
  };
}
