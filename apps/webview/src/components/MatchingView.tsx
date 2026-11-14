import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  ApiClient,
  Candidates,
  Offer,
  OfferImport,
  Product,
  SourceDocument,
  Unit,
  Workspace,
} from "../api/client";
import { groupOffers, type OfferGroup, type StepId } from "../journey/progress";
import { featureLabel, formatMoney } from "../review/costs";
import { formatDecimal, priceName } from "../review/format";
import { ErrorNotice } from "./ErrorNotice";
import { NextStep } from "./NextStep";

interface Props {
  client: ApiClient;
  workspace: Workspace;
  initialDocumentId?: string | null | undefined;
  onGo?: ((step: StepId, documentId?: string) => void) | undefined;
  onProgress?: (() => void) | undefined;
}

const READY = new Set(["completed", "review_required"]);

function unitText(units: readonly Unit[], code: string | null | undefined): string {
  if (!code) return "—";
  return units.find((u) => u.code === code)?.label_ru ?? code;
}

/** "Опт 430,00 RUB · Розница 490,00 RUB / шт" — every original price of the line. */
export function groupPrices(group: OfferGroup, units: readonly Unit[]): string {
  const prices = group.offers
    .map((o) => {
      const name = priceName(o.price_field_key);
      return name ? `${name} ${formatMoney(o.price)}` : formatMoney(o.price);
    })
    .join(" · ");
  return `${prices} / ${unitText(units, group.offers[0]?.unit)}`;
}

export function MatchingView({ client, workspace, initialDocumentId, onGo, onProgress }: Props) {
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [documentId, setDocumentId] = useState(initialDocumentId ?? "");
  const [offers, setOffers] = useState<Offer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [imported, setImported] = useState<OfferImport | null>(null);
  const [selectedLine, setSelectedLine] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const state = { cancelled: false };
    void (async () => {
      try {
        const [page, unitList] = await Promise.all([
          client.listDocuments(workspace.id),
          client.listUnits(),
        ]);
        if (state.cancelled) return;
        const ready = page.items.filter((d) => READY.has(d.status)).reverse();
        setDocuments(ready);
        setUnits(unitList);
        setDocumentId((current) => current || ready[0]?.id || "");
      } catch (e: unknown) {
        if (!state.cancelled) setError(e);
      }
    })();
    return () => {
      state.cancelled = true;
    };
  }, [client, workspace.id]);

  const reload = useCallback(() => {
    setTick((t) => t + 1);
    onProgress?.();
  }, [onProgress]);

  useEffect(() => {
    if (!documentId) return;
    const state = { cancelled: false };
    void (async () => {
      try {
        const [list, catalog] = await Promise.all([
          client.listAllOffers(workspace.id, documentId),
          client.listAllProducts(workspace.id),
        ]);
        if (state.cancelled) return;
        setOffers(list);
        setProducts(catalog);
        setLoaded(true);
      } catch (e: unknown) {
        if (!state.cancelled) setError(e);
      }
    })();
    return () => {
      state.cancelled = true;
    };
  }, [client, workspace.id, documentId, tick]);

  async function createOffers() {
    setBusy(true);
    setError(null);
    try {
      setImported(await client.createOffers(documentId));
      reload();
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const productName = useMemo(() => {
    const names = new Map(products.map((p) => [p.id, p.canonical_name]));
    return (id: string | null | undefined) => (id ? (names.get(id) ?? "товар") : null);
  }, [products]);

  const groups = useMemo(() => groupOffers(offers), [offers]);
  const matched = groups.filter((g) => g.matched).length;
  const selected = groups.find((g) => g.lineId === selectedLine) ?? null;
  const documentName = documents.find((d) => d.id === documentId)?.original_name ?? "";

  function select(lineId: string) {
    setSelectedLine(lineId);
    window.requestAnimationFrame(() => {
      panelRef.current?.focus();
    });
  }

  function nextStep() {
    if (documents.length === 0) {
      return (
        <NextStep
          title="загрузите и обработайте прайс"
          action={{ label: "К документам", onClick: () => onGo?.("import") }}
        >
          Сопоставлять пока нечего: нет обработанных документов.
        </NextStep>
      );
    }
    if (loaded && offers.length === 0) {
      return (
        <NextStep
          title={`создайте предложения из строк «${documentName}»`}
          action={{
            label: "Создать предложения из строк",
            onClick: () => void createOffers(),
            disabled: busy || !documentId,
          }}
        >
          Каждая строка прайса станет предложением поставщика со всеми исходными ценами (например,
          опт и розница).
        </NextStep>
      );
    }
    if (matched === 0) {
      return (
        <NextStep title="сопоставьте строки с товарами каталога">
          Нажмите на строку: появятся кандидаты с причинами. Подтвердите подходящий товар или
          создайте новый товар из строки. Связь появляется только после вашего решения.
        </NextStep>
      );
    }
    return (
      <NextStep
        title="сравните предложения"
        action={{ label: "Перейти к сравнению", onClick: () => onGo?.("compare") }}
        secondary={{
          label: "Обновить из проверенных строк",
          onClick: () => void createOffers(),
          disabled: busy,
        }}
      >
        Сопоставлено {matched} из {groups.length} строк. Для сравнения достаточно нужных вам
        товаров; остальные строки можно оставить.
      </NextStep>
    );
  }

  return (
    <section aria-labelledby="matching-title">
      <h2 id="matching-title">Сопоставление · {workspace.name}</h2>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      {nextStep()}
      <div className="toolbar">
        <label>
          Документ{" "}
          <select
            value={documentId}
            onChange={(e) => {
              setSelectedLine(null);
              setImported(null);
              setLoaded(false);
              setDocumentId(e.target.value);
            }}
          >
            {documents.length === 0 ? <option value="">Нет обработанных документов</option> : null}
            {documents.map((d) => (
              <option key={d.id} value={d.id}>
                {d.original_name}
              </option>
            ))}
          </select>
        </label>
        <span className="meta">
          {groups.length
            ? `Строк прайса: ${String(groups.length)} · сопоставлено: ${String(matched)}`
            : ""}
        </span>
      </div>
      {imported ? (
        <div className="notice" role="status">
          Готово: строк прайса — {String(groups.length)}. Цен поставщика создано{" "}
          {String(imported.created)}, обновлено по проверке {String(imported.updated)}, без
          изменений {String(imported.existing)} (у строки может быть несколько цен — опт и розница).
          Пропущено строк: {String(imported.skipped.length)}.
          {imported.skipped.length ? (
            <details>
              <summary>Почему пропущены строки</summary>
              <ul>
                {imported.skipped.map((s) => (
                  <li key={s.line_id}>
                    Строка {String(s.ordinal)}: {s.reason}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
      {groups.length ? (
        <div className="review-layout">
          <div className="review-table">
            <table aria-label="Строки прайса">
              <thead>
                <tr>
                  <th scope="col">№</th>
                  <th scope="col">Наименование</th>
                  <th scope="col">Исходные цены</th>
                  <th scope="col">Товар каталога</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((g) => (
                  <tr
                    key={g.lineId}
                    aria-selected={g.lineId === selectedLine}
                    className={g.lineId === selectedLine ? "row-selected" : undefined}
                    onClick={() => {
                      select(g.lineId);
                    }}
                  >
                    <td>{g.ordinal}</td>
                    <td>
                      <button
                        type="button"
                        className="link"
                        onClick={() => {
                          select(g.lineId);
                        }}
                      >
                        {g.name}
                      </button>
                      {g.offers[0]?.supplier_sku ? (
                        <div className="meta">Артикул {g.offers[0].supplier_sku}</div>
                      ) : null}
                    </td>
                    <td>
                      {groupPrices(g, units)}
                      {g.offers.some((o) => !o.price_confirmed) ? (
                        <div className="meta">цена не подтверждена при проверке</div>
                      ) : null}
                    </td>
                    <td>
                      {g.partial ? (
                        <span className="badge status-review_required">частично</span>
                      ) : g.matched ? (
                        <span className="badge status-completed">{productName(g.matched)}</span>
                      ) : (
                        <span className="muted">не сопоставлено</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selected ? (
            <LinePanel
              key={`${selected.lineId}-${selected.offers.map((o) => `${String(o.version)}.${String(o.decisions.length)}`).join("-")}`}
              panelRef={panelRef}
              client={client}
              group={selected}
              units={units}
              productName={productName}
              onChanged={reload}
              onClose={() => {
                setSelectedLine(null);
              }}
              onNextLine={
                groups.some((g) => !g.matched && g.lineId !== selected.lineId)
                  ? () => {
                      const after = groups.filter(
                        (g) => !g.matched && g.ordinal > selected.ordinal,
                      );
                      const target = after[0] ?? groups.find((g) => !g.matched);
                      if (target) select(target.lineId);
                    }
                  : undefined
              }
            />
          ) : (
            <aside className="review-side card">
              <p className="muted">Нажмите на строку, чтобы увидеть кандидатов из каталога.</p>
            </aside>
          )}
        </div>
      ) : null}
    </section>
  );
}

interface PanelProps {
  client: ApiClient;
  group: OfferGroup;
  units: readonly Unit[];
  productName: (id: string | null | undefined) => string | null;
  onChanged: () => void;
  onClose?: (() => void) | undefined;
  onNextLine?: (() => void) | undefined;
  panelRef?: React.RefObject<HTMLElement | null> | undefined;
}

/** Matching of one price-list line. Decisions apply to every original price of the line. */
export function LinePanel({
  client,
  group,
  units,
  productName,
  onChanged,
  onClose,
  onNextLine,
  panelRef,
}: PanelProps) {
  const first = group.offers[0];
  const [data, setData] = useState<Candidates | null>(null);
  const [reason, setReason] = useState("");
  const [baseUnit, setBaseUnit] = useState(first?.unit ?? "pc");
  const [packValue, setPackValue] = useState(first?.pack?.content.value ?? "");
  const [packUnit, setPackUnit] = useState(first?.pack?.content.unit ?? "kg");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!first) return undefined;
    const state = { cancelled: false };
    client
      .offerCandidates(first.id)
      .then((d) => {
        if (!state.cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!state.cancelled) setError(e);
      });
    return () => {
      state.cancelled = true;
    };
  }, [client, first]);

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await run();
      onChanged();
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  /** The same decision for every price of the line, in order (each is its own record). */
  async function decideAll(decision: "accept" | "reject" | "revoke", productId: string) {
    for (const offer of group.offers) {
      if (decision === "accept" && offer.matched_product_id === productId) continue;
      if (decision === "revoke" && offer.matched_product_id !== productId) continue;
      await client.decideMatch(offer.id, {
        decision,
        product_id: productId,
        reason: reason.trim() || null,
      });
    }
  }

  async function createProduct() {
    const [head, ...rest] = group.offers;
    if (!head) return;
    const product = await client.productFromOffer(head.id, { base_unit_code: baseUnit });
    for (const offer of rest) {
      await client.decideMatch(offer.id, {
        decision: "accept",
        product_id: product.id,
        reason: "та же строка прайса",
      });
    }
  }

  const matched = group.matched;
  const unit = first?.unit ?? null;

  return (
    <aside
      className="review-side card drawer"
      aria-label="Сопоставление строки"
      ref={panelRef}
      tabIndex={-1}
    >
      {onClose ? (
        <button type="button" className="drawer-close" onClick={onClose}>
          Закрыть
        </button>
      ) : null}
      <h3>{group.name}</h3>
      <p className="meta">
        Строка {group.ordinal} · {groupPrices(group, units)}
        {group.offers.length > 1
          ? ` · решение применяется ко всем ${String(group.offers.length)} ценам строки, цены сохраняются отдельно`
          : ""}
      </p>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      {matched ? (
        <div className="notice" role="status">
          Сопоставлено: <strong>{productName(matched)}</strong>{" "}
          {onNextLine ? (
            <button type="button" className="primary" onClick={onNextLine}>
              Следующая несопоставленная строка
            </button>
          ) : null}{" "}
          <button
            type="button"
            disabled={busy}
            onClick={() => void act(() => decideAll("revoke", matched))}
          >
            Отменить сопоставление
          </button>
        </div>
      ) : null}

      <h4>Кандидаты из каталога</h4>
      {data === null ? <p className="muted">Подбор кандидатов…</p> : null}
      {data && data.candidates.length === 0 ? (
        <p className="muted">
          Подходящих товаров в каталоге нет — создайте товар из этой строки (ниже).
        </p>
      ) : null}
      {data?.candidates.length ? (
        <label className="wide">
          Причина решения (обязательна, если сопоставитель нашёл конфликт)
          <textarea
            value={reason}
            maxLength={1000}
            rows={2}
            onChange={(e) => {
              setReason(e.target.value);
            }}
          />
        </label>
      ) : null}
      <ul className="list">
        {data?.candidates.map((c) => (
          <li
            key={c.product_id}
            className={c.blocked ? "candidate candidate-blocked" : "candidate"}
          >
            <div>
              <strong>{c.canonical_name}</strong>{" "}
              <span className={`badge ${c.blocked ? "status-failed" : "status-completed"}`}>
                {c.blocked ? "конфликт" : `оценка ${formatDecimal(c.score)}`}
              </span>
            </div>
            <ul className="meta features">
              {c.features.map((f, i) => (
                <li key={`${f.code}-${String(i)}`}>
                  {f.blocking ? "⛔ " : ""}
                  {featureLabel(f.code)}: {f.detail}
                </li>
              ))}
            </ul>
            <div className="actions">
              <button
                type="button"
                className="primary"
                disabled={busy || Boolean(matched) || (c.blocked && !reason.trim())}
                onClick={() => void act(() => decideAll("accept", c.product_id))}
              >
                Подтвердить
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void act(() => decideAll("reject", c.product_id))}
              >
                Отклонить
              </button>
            </div>
          </li>
        ))}
      </ul>

      {matched ? null : (
        <form
          className="inline"
          onSubmit={(e) => {
            e.preventDefault();
            void act(createProduct);
          }}
        >
          <label>
            Нет подходящего? Новый товар, сравнивать цену за{" "}
            <select
              value={baseUnit}
              onChange={(e) => {
                setBaseUnit(e.target.value);
              }}
            >
              {units.map((u) => (
                <option key={u.code} value={u.code}>
                  {u.label_ru}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={busy}>
            Создать товар из строки
          </button>
          <span className="meta">
            «шт» — сравнение за штуку/мешок; «кг», «м²» — за единицу меры (нужна фасовка).
          </span>
        </form>
      )}

      {unit ? (
        <>
          <h4>Фасовка</h4>
          <p className="meta">
            Нужна, если товар учитывается не в той единице, в которой указана цена (например, цена
            за мешок, а сравнивать нужно за кг). Из названия она только предлагается.
          </p>
          <form
            className="inline"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                for (const offer of group.offers) {
                  await client.setOfferPack(offer.id, offer.version, {
                    value: packValue.trim().replace(",", "."),
                    unit: packUnit,
                  });
                }
              });
            }}
          >
            <label>
              Фасовка: в 1 {unitText(units, unit)}{" "}
              <input
                aria-label="Количество в единице цены"
                value={packValue}
                inputMode="decimal"
                size={6}
                onChange={(e) => {
                  setPackValue(e.target.value);
                }}
              />
            </label>
            <select
              aria-label="Единица фасовки"
              value={packUnit}
              onChange={(e) => {
                setPackUnit(e.target.value);
              }}
            >
              {units
                .filter((u) => u.code !== unit)
                .map((u) => (
                  <option key={u.code} value={u.code}>
                    {u.label_ru}
                  </option>
                ))}
            </select>
            <button type="submit" disabled={busy || !packValue.trim()}>
              {first?.pack?.confirmed ? "Изменить фасовку" : "Подтвердить фасовку"}
            </button>
            {first?.pack && !first.pack.confirmed ? (
              <span className="meta">
                из названия: «{first.pack.evidence}» = {formatDecimal(first.pack.content.value)}{" "}
                {unitText(units, first.pack.content.unit)}
              </span>
            ) : null}
          </form>
        </>
      ) : null}

      {group.offers.some((o) => o.decisions.length) ? (
        <>
          <h4>История решений</h4>
          <ol className="history meta">
            {group.offers.flatMap((o) =>
              o.decisions.map((d) => (
                <li key={d.id}>
                  {priceName(o.price_field_key) ? `${priceName(o.price_field_key)}: ` : ""}
                  {d.decision === "accept"
                    ? "подтверждено"
                    : d.decision === "reject"
                      ? "отклонено"
                      : "отменено"}{" "}
                  — {productName(d.product_id)} · {new Date(d.decided_at).toLocaleString("ru-RU")}
                  {d.override ? " · вопреки конфликту" : ""}
                  {d.reason ? ` · ${d.reason}` : ""}
                </li>
              )),
            )}
          </ol>
        </>
      ) : null}
    </aside>
  );
}
