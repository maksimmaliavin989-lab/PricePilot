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
  formatMoney,
  rankKey,
  termsFromDraft,
  type RowStatus,
  type TermsDraft,
} from "../review/costs";
import { formatDecimal } from "../review/format";
import { CostSettings } from "./CostSettings";
import { ErrorNotice } from "./ErrorNotice";

interface Props {
  client: ApiClient;
  workspace: Workspace;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export function ComparisonView({ client, workspace }: Props) {
  const [list, setList] = useState<ComparisonSummary[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState(workspace.base_currency);
  const [asOf, setAsOf] = useState(today);
  const [showSettings, setShowSettings] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const state = { cancelled: false };
    void (async () => {
      try {
        const page = await client.listComparisons(workspace.id);
        if (!state.cancelled) setList([...page.items].reverse());
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
    } catch (e: unknown) {
      setError(e);
    }
  }

  return (
    <section aria-labelledby="comparison-title">
      <h2 id="comparison-title">Сравнение · {workspace.name}</h2>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
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
      {list.length ? (
        <div className="toolbar" role="list" aria-label="Сравнения">
          {list.map((c) => (
            <button
              key={c.id}
              type="button"
              role="listitem"
              aria-current={c.id === openId ? "true" : undefined}
              onClick={() => {
                setOpenId(c.id);
              }}
            >
              {c.name} · {c.target_currency} · {c.as_of}
            </button>
          ))}
        </div>
      ) : (
        <p className="muted">Сравнений пока нет.</p>
      )}
      {openId ? (
        <ComparisonDetail
          key={openId}
          client={client}
          workspace={workspace}
          comparisonId={openId}
        />
      ) : null}
    </section>
  );
}

interface DetailProps {
  client: ApiClient;
  workspace: Workspace;
  comparisonId: string;
}

export function ComparisonDetail({ client, workspace, comparisonId }: DetailProps) {
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

  const run = useCallback(async (action: () => Promise<Comparison>) => {
    setBusy(true);
    setError(null);
    try {
      setComparison(await action());
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }, []);

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
            {products
              .filter((p) => !inComparison.has(p.id))
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.canonical_name}
                </option>
              ))}
          </select>
        </label>
        <label>
          Нужно{" "}
          <input
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
      {comparison.items.length === 0 ? (
        <p className="muted">
          Добавьте товары каталога. Предложения появятся здесь после сопоставления.
        </p>
      ) : null}
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
    <section aria-label={item.product_name}>
      <h4>
        {item.product_name} · нужно {formatDecimal(item.required_quantity.value)}{" "}
        {unitText(item.base_unit_code)}
      </h4>
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
                {supplierName(o.supplier_id)}: {o.name} — {formatMoney(o.price)} /{" "}
                {unitText(o.unit)}
              </option>
            ))}
        </select>
        <button type="submit" disabled={busy || !offerId}>
          Добавить предложение
        </button>
        {offers.length === 0 ? (
          <span className="meta">Нет сопоставленных предложений — откройте «Сопоставление».</span>
        ) : null}
      </form>
      {sorted.length ? (
        <table>
          <thead>
            <tr>
              <th scope="col">Поставщик и предложение</th>
              <th scope="col">Цена</th>
              <th scope="col">Заказ</th>
              <th scope="col">Итог</th>
              <th scope="col">За {unitText(item.base_unit_code)}</th>
              <th scope="col">Статус</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((co) => (
              <OfferRows
                key={co.id}
                co={co}
                open={openId === co.id}
                onToggle={() => {
                  setOpenId(openId === co.id ? null : co.id);
                }}
                policy={policy}
                currency={comparison.target_currency}
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
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

interface RowProps {
  co: ComparisonOffer;
  open: boolean;
  onToggle: () => void;
  policy: CostPolicy;
  currency: string;
  supplierName: (id: string | null | undefined) => string;
  unitText: (code: string | null | undefined) => string;
  busy: boolean;
  onSave: (terms: ComparisonOffer["terms"]) => Promise<void>;
}

function statusOf(s: CostSnapshot | null | undefined): { text: string; className: string } {
  if (!s) return { text: "не рассчитано", className: "status-processing" };
  if (s.complete) return { text: "полный расчёт", className: "status-completed" };
  if (!s.total) return { text: "есть неизвестные", className: "status-failed" };
  return { text: "нужны подтверждения", className: "status-review_required" };
}

function OfferRows({
  co,
  open,
  onToggle,
  policy,
  currency,
  supplierName,
  unitText,
  busy,
  onSave,
}: RowProps) {
  const s = co.snapshot;
  const status = statusOf(s);
  return (
    <>
      <tr>
        <td>
          <button type="button" className="link" aria-expanded={open} onClick={onToggle}>
            {supplierName(co.offer.supplier_id)}: {co.offer.name}
          </button>
        </td>
        <td>
          {formatMoney(co.offer.price)} / {unitText(co.offer.unit)}
        </td>
        <td>
          {s ? `${formatDecimal(s.ordered.value)} ${unitText(s.ordered.unit)}` : "—"}
          {s ? (
            <div className="meta">
              = {formatDecimal(s.purchased.value)} {unitText(s.purchased.unit)}
            </div>
          ) : null}
        </td>
        <td>
          {s ? formatMoney(s.total) : "—"}
          {s && !s.total ? (
            <div className="meta">известная часть: {formatMoney(s.known_subtotal)}</div>
          ) : null}
        </td>
        <td>{s ? formatMoney(s.unit_cost) : "—"}</td>
        <td>
          <span className={`badge ${status.className}`}>{status.text}</span>
        </td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={6}>
            {s ? <Breakdown snapshot={s} /> : null}
            <TermsEditor policy={policy} co={co} currency={currency} busy={busy} onSave={onSave} />
          </td>
        </tr>
      ) : null}
    </>
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
                {c.origin ? (ORIGIN_LABELS[c.origin] ?? c.origin) : ""}
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
  { value: "unset", label: "не указано" },
  { value: "known", label: "значение" },
  { value: "unknown", label: "неизвестно" },
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
      <table className="wide">
        <thead>
          <tr>
            <th scope="col">Компонент</th>
            <th scope="col">Статус</th>
            <th scope="col">Значение</th>
            <th scope="col">Валюта</th>
            <th scope="col">Источник</th>
          </tr>
        </thead>
        <tbody>
          {draft.rows.map((row, index) => (
            <tr key={row.type.code}>
              <td>
                {row.type.label}
                {row.type.required ? <span className="meta"> · обязателен</span> : null}
                <div className="meta">{row.type.basis === "percent" ? "%" : "сумма"}</div>
              </td>
              <td>
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
              </td>
              <td>
                <input
                  aria-label={`${row.type.label}: значение`}
                  value={row.value}
                  inputMode="decimal"
                  size={8}
                  disabled={row.status !== "known"}
                  onChange={(e) => {
                    update(index, { value: e.target.value });
                  }}
                />
              </td>
              <td>
                {row.type.basis === "percent" ? (
                  "—"
                ) : (
                  <input
                    aria-label={`${row.type.label}: валюта`}
                    value={row.currency}
                    maxLength={3}
                    size={4}
                    disabled={row.status !== "known"}
                    onChange={(e) => {
                      update(index, { currency: e.target.value });
                    }}
                  />
                )}
              </td>
              <td>
                <input
                  aria-label={`${row.type.label}: источник`}
                  value={row.source}
                  maxLength={500}
                  placeholder={
                    row.type.category === "tax" || row.type.category === "duty"
                      ? "обязательно: откуда ставка"
                      : "договор, счёт…"
                  }
                  onChange={(e) => {
                    update(index, { source: e.target.value });
                  }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
      <label>
        Цена включает НДС
        <select
          value={draft.priceIncludesVat}
          onChange={(e) => {
            setDraft({
              ...draft,
              priceIncludesVat: e.target.value as TermsDraft["priceIncludesVat"],
            });
          }}
        >
          <option value="unknown">неизвестно</option>
          <option value="yes">да</option>
          <option value="no">нет</option>
        </select>
      </label>
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
