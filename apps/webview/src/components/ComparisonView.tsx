import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  ApiClient,
  Comparison,
  ComparisonItem,
  ComparisonOffer,
  ComparisonSummary,
  CostPolicy,
  CostSnapshot,
  Offer,
  Product,
  Supplier,
  Unit,
  Workspace,
} from "../api/client";
import {
  ORIGIN_LABELS,
  UNKNOWN,
  draftFromTerms,
  isPrimaryRow,
  formatMoney,
  rankKey,
  termsFromDraft,
  type RowStatus,
  type TermsDraft,
} from "../review/costs";
import { formatDecimal } from "../review/format";
import { CostSettings } from "./CostSettings";
import { ErrorNotice } from "./ErrorNotice";
import { NextStep } from "./NextStep";
import type { StepId } from "../journey/progress";
import { priceName } from "../review/format";

interface Props {
  client: ApiClient;
  workspace: Workspace;
  onGo?: ((step: StepId, documentId?: string) => void) | undefined;
  onProgress?: (() => void) | undefined;
}

function today(): string {
  const d = new Date(); // the user's local calendar date, not UTC
  return `${String(d.getFullYear())}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function ComparisonView({ client, workspace, onGo, onProgress }: Props) {
  const [matchedProducts, setMatchedProducts] = useState<number | null>(null);
  const [offerCount, setOfferCount] = useState<number | null>(null);
  const [list, setList] = useState<ComparisonSummary[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState(workspace.base_currency);
  const [asOf, setAsOf] = useState(today);
  const [showSettings, setShowSettings] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const state = { cancelled: false };
    void (async () => {
      try {
        const [page, offers] = await Promise.all([
          client.listComparisons(workspace.id),
          client.listAllOffers(workspace.id),
        ]);
        if (state.cancelled) return;
        const items = [...page.items].reverse();
        setList(items);
        setMatchedProducts(new Set(offers.map((o) => o.matched_product_id).filter(Boolean)).size);
        setOfferCount(offers.length);
        setOpenId((current) => current ?? items[0]?.id ?? null);
      } catch (e: unknown) {
        if (!state.cancelled) setError(e);
      }
    })();
    return () => {
      state.cancelled = true;
    };
  }, [client, workspace.id, tick]);

  async function create() {
    setError(null);
    try {
      const created = await client.createComparison({
        workspace_id: workspace.id,
        name: name.trim(),
        target_currency: currency.trim().toUpperCase(),
        as_of: asOf,
      });
      setName("");
      setTick((t) => t + 1);
      setOpenId(created.id);
      setShowNew(false);
    } catch (e: unknown) {
      setError(e);
    }
  }

  return (
    <section aria-labelledby="comparison-title">
      <header className="page-head">
        <div>
          <span className="eyebrow">Шаг 4 из 4 · {workspace.name}</span>
          <h1 id="comparison-title">Сравнение поставщиков</h1>
          <p className="lede">
            Полная стоимость с доставкой и фасовкой, в пересчёте на единицу товара. Неизвестное не
            считается нулём.
          </p>
        </div>
      </header>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      {offerCount === 0 ? (
        <NextStep
          title="начните с загрузки прайса поставщика"
          action={{ label: "К документам", onClick: () => onGo?.("import") }}
        >
          Сравнивать пока нечего. Путь: загрузить прайс → проверить строки → сопоставить с каталогом
          → сравнить.
        </NextStep>
      ) : matchedProducts === 0 ? (
        <NextStep
          title="сначала сопоставьте строки прайса с товарами каталога"
          action={{ label: "Перейти к сопоставлению", onClick: () => onGo?.("match") }}
        >
          Сравниваются только предложения, сопоставленные с товаром каталога. Откройте
          «Сопоставление», выберите строку и подтвердите кандидата или создайте товар из строки.
        </NextStep>
      ) : list.length === 0 && matchedProducts ? (
        <NextStep title="создайте сравнение">
          Введите название в форме ниже и нажмите «Создать». Товаров с предложениями:{" "}
          {matchedProducts}.
        </NextStep>
      ) : null}
      <div className="toolbar">
        <button
          type="button"
          aria-expanded={showSettings}
          onClick={() => {
            setShowSettings((v) => !v);
          }}
        >
          {showSettings
            ? "Скрыть настройки расчёта"
            : "Настройки расчёта: компоненты, курсы, ставки"}
        </button>
      </div>
      {showSettings ? <CostSettings client={client} workspace={workspace} /> : null}
      {list.length && !showNew ? (
        <div className="chips" role="group" aria-label="Сравнения">
          {list.map((c) => (
            <button
              key={c.id}
              type="button"
              className="chip"
              aria-pressed={c.id === openId}
              onClick={() => {
                setOpenId(c.id);
              }}
            >
              {c.name} · {c.as_of}
            </button>
          ))}
          <button
            type="button"
            className="chip"
            onClick={() => {
              setShowNew(true);
            }}
          >
            + Новое сравнение
          </button>
        </div>
      ) : null}
      {(matchedProducts && !list.length) || showNew ? (
        <form
          className="card form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <label className="wide">
            Новое сравнение
            <input
              value={name}
              required
              maxLength={200}
              placeholder="Например: цемент на объект"
              onChange={(e) => {
                setName(e.target.value);
              }}
            />
          </label>
          <label>
            Валюта расчёта
            <input
              value={currency}
              required
              maxLength={3}
              onChange={(e) => {
                setCurrency(e.target.value);
              }}
            />
          </label>
          <label>
            Дата курсов и ставок
            <input
              type="date"
              value={asOf}
              required
              onChange={(e) => {
                setAsOf(e.target.value);
              }}
            />
          </label>
          <div className="actions">
            <button type="submit" className="primary" disabled={!name.trim()}>
              Создать
            </button>
          </div>
        </form>
      ) : null}
      {openId ? (
        <ComparisonDetail
          key={openId}
          client={client}
          workspace={workspace}
          comparisonId={openId}
          onGo={onGo}
          onProgress={onProgress}
        />
      ) : null}
    </section>
  );
}

interface DetailProps {
  client: ApiClient;
  workspace: Workspace;
  comparisonId: string;
  onGo?: ((step: StepId, documentId?: string) => void) | undefined;
  onProgress?: (() => void) | undefined;
}

export function ComparisonDetail({
  client,
  workspace,
  comparisonId,
  onGo,
  onProgress,
}: DetailProps) {
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [policy, setPolicy] = useState<CostPolicy | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [offers, setOffers] = useState<Offer[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [productId, setProductId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const state = { cancelled: false };
    void (async () => {
      try {
        const [c, p, prods, offs, sups, us] = await Promise.all([
          client.getComparison(comparisonId),
          client.costPolicy(workspace.id),
          client.listAllProducts(workspace.id),
          client.listAllOffers(workspace.id),
          client.listSuppliers({ workspaceId: workspace.id, includeArchived: true }),
          client.listUnits(),
        ]);
        if (state.cancelled) return;
        setComparison(c);
        setPolicy(p);
        setProducts(prods);
        setOffers(offs);
        setSuppliers(sups.items);
        setUnits(us);
      } catch (e: unknown) {
        if (!state.cancelled) setError(e);
      }
    })();
    return () => {
      state.cancelled = true;
    };
  }, [client, workspace.id, comparisonId]);

  const run = useCallback(
    async (action: () => Promise<Comparison>) => {
      setBusy(true);
      setError(null);
      try {
        setComparison(await action());
        onProgress?.();
      } catch (e: unknown) {
        setError(e);
      } finally {
        setBusy(false);
      }
    },
    [onProgress],
  );

  const supplierName = useMemo(() => {
    const names = new Map(suppliers.map((s) => [s.id, s.name]));
    return (id: string | null | undefined) => (id ? (names.get(id) ?? "—") : "без поставщика");
  }, [suppliers]);

  const unitText = useCallback(
    (code: string | null | undefined) =>
      code ? (units.find((u) => u.code === code)?.label_ru ?? code) : "—",
    [units],
  );

  if (!comparison || !policy) {
    return (
      <>
        <ErrorNotice error={error} />
        <p className="muted">Загрузка сравнения…</p>
      </>
    );
  }

  const chosen = products.find((p) => p.id === productId);
  const inComparison = new Set(comparison.items.map((i) => i.product_id));
  const offerCount = new Map<string, number>();
  for (const o of offers) {
    if (o.matched_product_id) {
      offerCount.set(o.matched_product_id, (offerCount.get(o.matched_product_id) ?? 0) + 1);
    }
  }
  const candidates = products.filter((p) => offerCount.has(p.id) && !inComparison.has(p.id));
  const attachedCount = comparison.items.reduce((n, i) => n + i.offers.length, 0);
  const snapshots = comparison.items.flatMap((i) => i.offers.map((o) => o.snapshot));
  const stale = attachedCount > 0 && snapshots.some((s) => !s);
  const unknowns = snapshots.filter((s) => s && !s.total).length;

  function guide() {
    if (offerCount.size === 0) {
      return (
        <NextStep
          title="сопоставьте строки прайса с товарами каталога"
          action={{ label: "Перейти к сопоставлению", onClick: () => onGo?.("match") }}
        >
          В сравнение попадают только сопоставленные предложения.
        </NextStep>
      );
    }
    if (comparison?.items.length === 0) {
      return (
        <NextStep title="добавьте товар и нужное количество">
          Выберите товар ниже (в списке — только товары с сопоставленными предложениями), укажите
          количество в его единице учёта и нажмите «Добавить позицию».
        </NextStep>
      );
    }
    if (attachedCount === 0) {
      return (
        <NextStep
          title="добавьте предложения поставщиков"
          action={{
            label: "Добавить все сопоставленные предложения",
            disabled: busy,
            onClick: () =>
              void run(async () => {
                let latest: Comparison | null = null;
                for (const item of comparison?.items ?? []) {
                  for (const o of offers.filter((x) => x.matched_product_id === item.product_id)) {
                    latest = await client.addComparisonOffer(item.id, {
                      offer_id: o.id,
                      components: [],
                      minimum_order: null,
                      order_multiple: null,
                      price_includes_vat: null,
                    });
                  }
                }
                return latest ?? client.getComparison(comparisonId);
              }),
          }}
        >
          Каждая исходная цена (опт, розница) станет отдельной строкой сравнения.
        </NextStep>
      );
    }
    if (stale) {
      return (
        <NextStep
          title="рассчитайте полную стоимость"
          action={{
            label: "Рассчитать",
            onClick: () => void run(() => client.calculateComparison(comparisonId)),
            disabled: busy,
          }}
        />
      );
    }
    if (unknowns > 0) {
      return (
        <NextStep title={`заполните неизвестные условия (${String(unknowns)} предл.)`}>
          Нажмите на поставщика в таблице: в расшифровке видно, чего не хватает (например,
          доставки). Укажите сумму или отметьте «не применяется» и сохраните — итог пересчитается.
        </NextStep>
      );
    }
    return (
      <NextStep title="выберите поставщика">
        Предложения отсортированы по стоимости базовой единицы; полные расчёты — выше. Нажмите на
        строку, чтобы увидеть расшифровку и источники.
      </NextStep>
    );
  }

  return (
    <div className="card">
      <h3>
        {comparison.name} · {comparison.target_currency} · курсы и ставки на {comparison.as_of}
      </h3>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      {guide()}
      <form
        className="inline"
        onSubmit={(e) => {
          e.preventDefault();
          if (!chosen) return;
          void run(() =>
            client.addComparisonItem(comparison.id, {
              product_id: chosen.id,
              required_quantity: {
                value: quantity.trim().replace(",", "."),
                unit: chosen.base_unit_code,
              },
            }),
          );
          setQuantity("");
        }}
      >
        <label>
          Товар{" "}
          <select
            value={productId}
            onChange={(e) => {
              setProductId(e.target.value);
            }}
          >
            <option value="">— выберите —</option>
            {candidates.map((p) => (
              <option key={p.id} value={p.id}>
                {p.canonical_name} — предложений: {offerCount.get(p.id) ?? 0}
              </option>
            ))}
          </select>
        </label>
        <label>
          Нужно{" "}
          <input
            aria-label="Нужное количество"
            value={quantity}
            inputMode="decimal"
            size={8}
            onChange={(e) => {
              setQuantity(e.target.value);
            }}
          />{" "}
          {chosen ? unitText(chosen.base_unit_code) : ""}
        </label>
        <button type="submit" disabled={busy || !chosen || !quantity.trim()}>
          Добавить позицию
        </button>
        <button
          type="button"
          className="primary"
          disabled={busy || comparison.items.length === 0}
          onClick={() => void run(() => client.calculateComparison(comparison.id))}
        >
          Рассчитать
        </button>
      </form>
      {comparison.items.map((item) => (
        <ItemBlock
          key={item.id}
          item={item}
          comparison={comparison}
          policy={policy}
          offers={offers.filter((o) => o.matched_product_id === item.product_id)}
          busy={busy}
          supplierName={supplierName}
          unitText={unitText}
          onRun={run}
          client={client}
        />
      ))}
    </div>
  );
}

interface ItemProps {
  client: ApiClient;
  item: ComparisonItem;
  comparison: Comparison;
  policy: CostPolicy;
  offers: Offer[];
  busy: boolean;
  supplierName: (id: string | null | undefined) => string;
  unitText: (code: string | null | undefined) => string;
  onRun: (action: () => Promise<Comparison>) => Promise<void>;
}

function ItemBlock({
  client,
  item,
  comparison,
  policy,
  offers,
  busy,
  supplierName,
  unitText,
  onRun,
}: ItemProps) {
  const [offerId, setOfferId] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const attached = new Set(item.offers.map((o) => o.offer.id));
  const sorted = [...item.offers].sort((a, b) => {
    const [ka, va] = rankKey(a.snapshot);
    const [kb, vb] = rankKey(b.snapshot);
    return ka - kb || va - vb;
  });

  return (
    <section aria-label={item.product_name} style={{ display: "grid", gap: 14 }}>
      <h2>
        {item.product_name} — нужно {formatDecimal(item.required_quantity.value)}{" "}
        {unitText(item.base_unit_code)}
      </h2>
      <form
        className="inline"
        onSubmit={(e) => {
          e.preventDefault();
          void onRun(() =>
            client.addComparisonOffer(item.id, {
              offer_id: offerId,
              components: [],
              minimum_order: null,
              order_multiple: null,
              price_includes_vat: null,
            }),
          );
          setOfferId("");
        }}
      >
        <select
          aria-label="Сопоставленное предложение"
          value={offerId}
          onChange={(e) => {
            setOfferId(e.target.value);
          }}
        >
          <option value="">— предложение —</option>
          {offers
            .filter((o) => !attached.has(o.id))
            .map((o) => (
              <option key={o.id} value={o.id}>
                {supplierName(o.supplier_id)}: {o.name} —{" "}
                {priceName(o.price_field_key) ? `${priceName(o.price_field_key)} ` : ""}
                {formatMoney(o.price)} / {unitText(o.unit)}
              </option>
            ))}
        </select>
        <button type="submit" disabled={busy || !offerId}>
          Добавить предложение
        </button>
        {offers.some((o) => !attached.has(o.id)) ? (
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() =>
              void onRun(async () => {
                let latest: Comparison | null = null;
                for (const o of offers.filter((x) => !attached.has(x.id))) {
                  latest = await client.addComparisonOffer(item.id, {
                    offer_id: o.id,
                    components: [],
                    minimum_order: null,
                    order_multiple: null,
                    price_includes_vat: null,
                  });
                }
                return latest ?? client.getComparison(comparison.id);
              })
            }
          >
            Добавить все предложения ({offers.filter((o) => !attached.has(o.id)).length})
          </button>
        ) : null}
        {offers.length === 0 ? (
          <span className="meta">Нет сопоставленных предложений — откройте «Сопоставление».</span>
        ) : null}
      </form>
      {sorted.length ? (
        <div className="offer-cards">
          {sorted.map((co, index) => (
            <OfferCard
              key={co.id}
              co={co}
              best={index === 0 && sorted.length > 1 && co.snapshot?.complete === true}
              open={openId === co.id}
              onToggle={() => {
                setOpenId(openId === co.id ? null : co.id);
              }}
              policy={policy}
              currency={comparison.target_currency}
              baseUnit={unitText(item.base_unit_code)}
              supplierName={supplierName}
              unitText={unitText}
              busy={busy}
              onSave={(terms) =>
                onRun(() => client.updateComparisonTerms(co.id, terms)).then(() =>
                  onRun(() => client.calculateComparison(comparison.id)),
                )
              }
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

interface CardProps {
  co: ComparisonOffer;
  best: boolean;
  open: boolean;
  onToggle: () => void;
  policy: CostPolicy;
  currency: string;
  baseUnit: string;
  supplierName: (id: string | null | undefined) => string;
  unitText: (code: string | null | undefined) => string;
  busy: boolean;
  onSave: (terms: ComparisonOffer["terms"]) => Promise<void>;
}

function statusOf(s: CostSnapshot | null | undefined): { text: string; className: string } {
  if (!s) return { text: "Не рассчитано", className: "status-processing" };
  if (s.complete) return { text: "Полный расчёт", className: "status-completed" };
  if (!s.total) return { text: "Нужно условие", className: "status-review_required" };
  return { text: "Нужны подтверждения", className: "status-review_required" };
}

/** One supplier offer: the cost per base unit first, what it is made of, what is missing. */
function OfferCard({
  co,
  best,
  open,
  onToggle,
  policy,
  currency,
  baseUnit,
  supplierName,
  unitText,
  busy,
  onSave,
}: CardProps) {
  const s = co.snapshot;
  const status = statusOf(s);
  const priceType = priceName(co.offer.price_field_key);
  const unknownLabels = s
    ? s.components.filter((c) => c.amount === null).map((c) => c.label.toLowerCase())
    : [];
  const otherWarnings = s ? s.warnings.filter((w) => w.code !== "UNKNOWN_COMPONENT") : [];
  return (
    <article
      className={best ? "offer-card best" : "offer-card"}
      aria-label={`${supplierName(co.offer.supplier_id)}: ${co.offer.name}`}
    >
      <div className="offer-head">
        <div>
          <div className="offer-supplier">
            {supplierName(co.offer.supplier_id)}
            {priceType ? ` · ${priceType}` : ""}
          </div>
          <strong>{co.offer.name}</strong>
          <div className="meta">
            {formatMoney(co.offer.price)} / {unitText(co.offer.unit)}
          </div>
        </div>
        <span className={best ? "badge best-pill" : `badge ${status.className}`}>
          {best ? "Выгоднее всего" : status.text}
        </span>
      </div>

      {s ? (
        <>
          <div>
            <div className={s.unit_cost ? "offer-price" : "offer-price unknown"}>
              {s.unit_cost ? formatMoney(s.unit_cost) : "? ₽"} <small>/ {baseUnit}</small>
            </div>
            <div className="meta">
              {s.total
                ? `Итого ${formatMoney(s.total)} · заказ ${formatDecimal(s.ordered.value)} ${unitText(s.ordered.unit)}`
                : `Известно: ${formatMoney(s.known_subtotal)} — без: ${unknownLabels.join(", ")}`}
            </div>
          </div>
          <ul className="offer-lines" aria-label="Из чего сложилась сумма">
            {s.components.map((c) => (
              <li key={c.code} className={c.amount === null ? "is-unknown" : undefined}>
                <span>
                  {c.label}
                  {c.amount === null ? "" : <span className="meta"> · {c.detail}</span>}
                </span>
                <span>
                  {c.amount === null
                    ? UNKNOWN
                    : formatMoney({ amount: c.amount, currency: c.currency })}
                </span>
              </li>
            ))}
          </ul>
          {otherWarnings.length ? (
            <ul className="reasons meta">
              {otherWarnings.map((w) => (
                <li key={w.message}>{w.message}</li>
              ))}
            </ul>
          ) : null}
        </>
      ) : (
        <p className="meta">Нажмите «Рассчитать», чтобы увидеть полную стоимость.</p>
      )}

      <div className="actions">
        <button
          type="button"
          className={s && !s.total ? "primary" : undefined}
          aria-expanded={open}
          onClick={onToggle}
        >
          {open ? "Скрыть условия" : s && !s.total ? "Указать условия" : "Условия и расшифровка"}
        </button>
      </div>
      {open ? (
        <div className="terms-box">
          <TermsEditor policy={policy} co={co} currency={currency} busy={busy} onSave={onSave} />
          {s ? (
            <details>
              <summary>Подробная расшифровка и источники</summary>
              <Breakdown snapshot={s} />
            </details>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

export function Breakdown({ snapshot }: { snapshot: CostSnapshot }) {
  return (
    <div>
      <table aria-label="Расшифровка расчёта">
        <thead>
          <tr>
            <th scope="col">Компонент</th>
            <th scope="col">Сумма</th>
            <th scope="col">Как получено</th>
            <th scope="col">Источник</th>
          </tr>
        </thead>
        <tbody>
          {snapshot.components.map((c) => (
            <tr key={c.code}>
              <td>{c.label}</td>
              <td>
                {c.amount === null
                  ? UNKNOWN
                  : formatMoney({ amount: c.amount, currency: c.currency })}
              </td>
              <td>
                {c.detail}
                {c.original ? <div className="meta">исходно {formatMoney(c.original)}</div> : null}
                {c.fx ? (
                  <div className="meta">
                    курс {String(c.fx.base)}/{String(c.fx.quote)} {String(c.fx.rate)} от{" "}
                    {String(c.fx.effective_date)} ({String(c.fx.source)})
                  </div>
                ) : null}
              </td>
              <td>
                {c.origin && c.amount !== null ? (ORIGIN_LABELS[c.origin] ?? c.origin) : ""}
                {c.source ? <div className="meta">{c.source}</div> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {snapshot.warnings.length ? (
        <ul className="reasons" aria-label="Предупреждения">
          {snapshot.warnings.map((w) => (
            <li key={w.message}>{w.message}</li>
          ))}
        </ul>
      ) : null}
      <p className="meta">
        Формула {snapshot.formula_version} · снимок {snapshot.id.slice(0, 8)} от{" "}
        {new Date(snapshot.created_at).toLocaleString("ru-RU")}
      </p>
    </div>
  );
}

const STATUS_OPTIONS: { value: RowStatus; label: string }[] = [
  { value: "unset", label: "— выберите —" },
  { value: "known", label: "указать сумму или %" },
  { value: "unknown", label: "пока неизвестно" },
  { value: "not_applicable", label: "не применяется" },
];

function TermsEditor({
  policy,
  co,
  currency,
  busy,
  onSave,
}: {
  policy: CostPolicy;
  co: ComparisonOffer;
  currency: string;
  busy: boolean;
  onSave: (terms: ComparisonOffer["terms"]) => Promise<void>;
}) {
  const [draft, setDraft] = useState<TermsDraft>(() => draftFromTerms(policy, co.terms, currency));
  const [problem, setProblem] = useState<string | null>(null);

  function update(index: number, patch: Partial<TermsDraft["rows"][number]>) {
    setDraft((d) => ({
      ...d,
      rows: d.rows.map((r, i) => (i === index ? { ...r, ...patch } : r)),
    }));
  }

  const unknown = new Set(co.snapshot?.unknown_components ?? []);
  const primary = draft.rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => isPrimaryRow(row, unknown));
  const extra = draft.rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => !isPrimaryRow(row, unknown));

  function renderRow({ row, index }: { row: TermsDraft["rows"][number]; index: number }) {
    const taxLike = row.type.category === "tax" || row.type.category === "duty";
    return (
      <div key={row.type.code} className="terms-row">
        <strong>{row.type.label}</strong>
        {row.type.required ? <span className="meta"> · нужен для итога</span> : null}
        {unknown.has(row.type.code) ? <span className="meta"> · сейчас неизвестно</span> : null}
        <div className="inline">
          <select
            aria-label={`${row.type.label}: статус`}
            value={row.status}
            onChange={(e) => {
              update(index, { status: e.target.value as RowStatus });
            }}
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {row.status === "known" ? (
            <>
              <input
                aria-label={`${row.type.label}: значение`}
                value={row.value}
                inputMode="decimal"
                size={8}
                placeholder={row.type.basis === "percent" ? "%" : "сумма"}
                onChange={(e) => {
                  update(index, { value: e.target.value });
                }}
              />
              {row.type.basis === "percent" ? (
                <span>%</span>
              ) : (
                <input
                  aria-label={`${row.type.label}: валюта`}
                  value={row.currency}
                  maxLength={3}
                  size={4}
                  onChange={(e) => {
                    update(index, { currency: e.target.value });
                  }}
                />
              )}
              <input
                aria-label={`${row.type.label}: источник`}
                value={row.source}
                maxLength={500}
                placeholder={taxLike ? "обязательно: откуда ставка" : "откуда (необязательно)"}
                onChange={(e) => {
                  update(index, { source: e.target.value });
                }}
              />
            </>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <form
      className="field-form"
      aria-label="Условия предложения"
      onSubmit={(e) => {
        e.preventDefault();
        const terms = termsFromDraft(draft);
        if (typeof terms === "string") {
          setProblem(terms);
          return;
        }
        setProblem(null);
        void onSave(terms);
      }}
    >
      <div className="wide">
        <h4>Что нужно для итога</h4>
        {primary.length ? (
          primary.map(renderRow)
        ) : (
          <p className="meta">Обязательных условий нет.</p>
        )}
        <label>
          Цена в прайсе включает НДС?{" "}
          <select
            value={draft.priceIncludesVat}
            onChange={(e) => {
              setDraft({
                ...draft,
                priceIncludesVat: e.target.value as TermsDraft["priceIncludesVat"],
              });
            }}
          >
            <option value="unknown">не знаю</option>
            <option value="yes">да</option>
            <option value="no">нет</option>
          </select>
        </label>
      </div>
      <details className="terms-extra wide">
        <summary>
          Дополнительные условия ({extra.map(({ row }) => row.type.label.toLowerCase()).join(", ")}
          {extra.length ? ", " : ""}минимальный заказ, кратность)
        </summary>
        {extra.map(renderRow)}
        <label>
          Минимальный заказ (в единицах цены)
          <input
            value={draft.minimumOrder}
            inputMode="decimal"
            onChange={(e) => {
              setDraft({ ...draft, minimumOrder: e.target.value });
            }}
          />
        </label>
        <label>
          Кратность заказа
          <input
            value={draft.orderMultiple}
            inputMode="decimal"
            onChange={(e) => {
              setDraft({ ...draft, orderMultiple: e.target.value });
            }}
          />
        </label>
      </details>
      {problem ? (
        <div className="notice notice-error" role="alert">
          {problem}
        </div>
      ) : null}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>
          Сохранить и пересчитать
        </button>
      </div>
    </form>
  );
}
