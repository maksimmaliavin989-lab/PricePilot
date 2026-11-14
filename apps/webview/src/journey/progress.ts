import type { Line, Offer } from "../api/client";
import { lineState } from "../review/lines";

/** The main path of the app: price list → reviewed lines → matched catalog → comparison. */
export type StepId = "import" | "review" | "match" | "compare";

/** "todo" — не начато, "active" — в процессе, "done" — готово. */
export type StepState = "todo" | "active" | "done";

export const STEP_STATE_LABELS: Record<StepState, string> = {
  todo: "не начато",
  active: "в процессе",
  done: "готово",
};

export interface StepView {
  id: StepId;
  title: string;
  hint: string;
  state: StepState;
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
  complete: number; // comparisons where every calculated offer is complete (nothing unknown)
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
  complete: 0,
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

function state(done: boolean, started: boolean): StepState {
  return done ? "done" : started ? "active" : "todo";
}

export function steps(p: Progress): StepView[] {
  return [
    {
      id: "import",
      title: "Загрузить прайс",
      hint: p.documents
        ? `обработано документов: ${String(p.documents)}`
        : p.processing
          ? "обрабатывается"
          : "файл поставщика",
      state: state(p.documents > 0, p.processing > 0),
    },
    {
      id: "review",
      title: "Проверить строки",
      hint: p.lines
        ? `проверено ${String(p.checkedLines)} из ${String(p.lines)}`
        : "цены и единицы из файла",
      state: state(p.lines > 0 && p.checkedLines === p.lines, p.checkedLines > 0),
    },
    {
      id: "match",
      title: "Сопоставить с каталогом",
      hint: p.offerLines
        ? `сопоставлено ${String(p.matchedLines)} из ${String(p.offerLines)} строк`
        : "строки прайса → товары",
      state: state(p.offerLines > 0 && p.matchedLines === p.offerLines, p.matchedLines > 0),
    },
    {
      id: "compare",
      title: "Сравнить предложения",
      hint: !p.comparisons
        ? "полная стоимость"
        : !p.calculated
          ? `сравнений: ${String(p.comparisons)}, не рассчитано`
          : p.complete
            ? `рассчитано полностью: ${String(p.complete)}`
            : "есть неизвестные условия",
      state: state(p.complete > 0, p.comparisons > 0),
    },
  ];
}

/** Where the user should go next: the first step that is not done. Matching is the one
 * exception: it never has to cover every line of a price list, so once something is matched
 * the user may move on to the comparison (the step stays "in progress"). */
export function currentStep(p: Progress): StepId {
  const blocking = steps(p).find(
    (s) => s.state === "todo" || (s.state === "active" && s.id !== "match"),
  );
  return blocking?.id ?? "compare";
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
    complete: details.filter((c) => {
      const snaps = c.items.flatMap((i) => i.offers.map((o) => o.snapshot));
      return snaps.length > 0 && snaps.every((snap) => snap?.complete === true);
    }).length,
  };
}
