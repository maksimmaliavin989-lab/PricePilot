import { useCallback, useEffect, useMemo, useState } from "react";

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
import { featureLabel, formatMoney } from "../review/costs";
import { formatDecimal, priceName } from "../review/format";
import { ErrorNotice } from "./ErrorNotice";

interface Props {
  client: ApiClient;
  workspace: Workspace;
}

const READY = new Set(["completed", "review_required"]);

function unitText(units: readonly Unit[], code: string | null | undefined): string {
  if (!code) return "—";
  return units.find((u) => u.code === code)?.label_ru ?? code;
}

export function MatchingView({ client, workspace }: Props) {
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [documentId, setDocumentId] = useState("");
  const [offers, setOffers] = useState<Offer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [imported, setImported] = useState<OfferImport | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

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

  const [tick, setTick] = useState(0);
  const reload = useCallback(() => {
    setTick((t) => t + 1);
  }, []);

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
        setOffers(
          [...list].sort(
            (a, b) =>
              a.line_ordinal - b.line_ordinal || a.price_field_key.localeCompare(b.price_field_key),
          ),
        );
        setProducts(catalog);
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

  const selected = offers.find((o) => o.id === selectedId) ?? null;

  return (
    <section aria-labelledby="matching-title">
      <h2 id="matching-title">Сопоставление · {workspace.name}</h2>
      <p className="muted">
        Предложения создаются из проверенных строк прайса. Кандидаты — только подсказки с причинами;
        связь появляется после вашего решения. Автоматическое сопоставление выключено до проверки
        порогов на реальных обезличенных прайсах.
      </p>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      <div className="toolbar">
        <label>
          Документ{" "}
          <select
            value={documentId}
            onChange={(e) => {
              setSelectedId(null);
              setImported(null);
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
        <button
          type="button"
          className="primary"
          disabled={!documentId || busy}
          onClick={() => void createOffers()}
        >
          Создать предложения из строк
        </button>
      </div>
      {imported ? (
        <div className="notice" role="status">
          Создано: {String(imported.created)}, уже было: {String(imported.existing)}, пропущено:{" "}
          {String(imported.skipped.length)}.
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
      {offers.length === 0 ? (
        <p className="muted">Предложений для этого документа пока нет.</p>
      ) : (
        <div className="review-layout">
          <table className="review-table">
            <thead>
              <tr>
                <th scope="col">Предложение</th>
                <th scope="col">Цена</th>
                <th scope="col">Фасовка</th>
                <th scope="col">Товар каталога</th>
              </tr>
            </thead>
            <tbody>
              {offers.map((o) => (
                <tr
                  key={o.id}
                  aria-selected={o.id === selectedId}
                  className={o.id === selectedId ? "row-selected" : undefined}
                  onClick={() => {
                    setSelectedId(o.id);
                  }}
                >
                  <td>
                    <button
                      type="button"
                      className="link"
                      onClick={() => {
                        setSelectedId(o.id);
                      }}
                    >
                      {o.name}
                    </button>
                    {o.supplier_sku ? <div className="meta">Артикул {o.supplier_sku}</div> : null}
                  </td>
                  <td>
                    {formatMoney(o.price)} / {unitText(units, o.unit)}
                    {priceName(o.price_field_key) ? (
                      <div className="meta">{priceName(o.price_field_key)}</div>
                    ) : null}
                    {o.price_confirmed ? null : <div className="meta">цена не подтверждена</div>}
                  </td>
                  <td>
                    {o.pack
                      ? `${formatDecimal(o.pack.content.value)} ${unitText(units, o.pack.content.unit)}`
                      : "—"}
                    {o.pack && !o.pack.confirmed ? (
                      <div className="meta">из названия, не подтверждена</div>
                    ) : null}
                  </td>
                  <td>
                    {o.matched_product_id ? (
                      <span className="badge status-completed">
                        {productName(o.matched_product_id)}
                      </span>
                    ) : (
                      <span className="muted">не сопоставлено</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {selected ? (
            <OfferPanel
              key={`${selected.id}-${String(selected.version)}-${String(selected.decisions.length)}`}
              client={client}
              offer={selected}
              units={units}
              productName={productName}
              onChanged={reload}
            />
          ) : (
            <aside className="review-side card">
              <p className="muted">Выберите предложение, чтобы увидеть кандидатов.</p>
            </aside>
          )}
        </div>
      )}
    </section>
  );
}

interface PanelProps {
  client: ApiClient;
  offer: Offer;
  units: readonly Unit[];
  productName: (id: string | null | undefined) => string | null;
  onChanged: () => void;
}

export function OfferPanel({ client, offer, units, productName, onChanged }: PanelProps) {
  const [data, setData] = useState<Candidates | null>(null);
  const [reason, setReason] = useState("");
  const [baseUnit, setBaseUnit] = useState(offer.unit ?? "pc");
  const [packValue, setPackValue] = useState(offer.pack?.content.value ?? "");
  const [packUnit, setPackUnit] = useState(offer.pack?.content.unit ?? "kg");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const state = { cancelled: false };
    client
      .offerCandidates(offer.id)
      .then((d) => {
        if (!state.cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!state.cancelled) setError(e);
      });
    return () => {
      state.cancelled = true;
    };
  }, [client, offer.id]);

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

  const matched = offer.matched_product_id;

  return (
    <aside className="review-side card" aria-label="Сопоставление предложения">
      <h3>{offer.name}</h3>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      {matched ? (
        <div className="notice" role="status">
          Сопоставлено: <strong>{productName(matched)}</strong>{" "}
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void act(() =>
                client.decideMatch(offer.id, {
                  decision: "revoke",
                  product_id: matched,
                  reason: reason.trim() || null,
                }),
              )
            }
          >
            Отменить сопоставление
          </button>
        </div>
      ) : null}

      {offer.unit ? (
        <form
          className="inline"
          onSubmit={(e) => {
            e.preventDefault();
            void act(() =>
              client.setOfferPack(offer.id, offer.version, {
                value: packValue.trim().replace(",", "."),
                unit: packUnit,
              }),
            );
          }}
        >
          <label>
            Фасовка: в 1 {unitText(units, offer.unit)}{" "}
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
              .filter((u) => u.code !== offer.unit)
              .map((u) => (
                <option key={u.code} value={u.code}>
                  {u.label_ru}
                </option>
              ))}
          </select>
          <button type="submit" disabled={busy || !packValue.trim()}>
            {offer.pack?.confirmed ? "Изменить" : "Подтвердить фасовку"}
          </button>
        </form>
      ) : null}

      <h4>Кандидаты</h4>
      {data === null ? <p className="muted">Подбор кандидатов…</p> : null}
      {data && data.candidates.length === 0 ? (
        <p className="muted">Подходящих товаров в каталоге нет.</p>
      ) : null}
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
                onClick={() =>
                  void act(() =>
                    client.decideMatch(offer.id, {
                      decision: "accept",
                      product_id: c.product_id,
                      reason: reason.trim() || null,
                    }),
                  )
                }
              >
                Подтвердить
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(() =>
                    client.decideMatch(offer.id, {
                      decision: "reject",
                      product_id: c.product_id,
                      reason: reason.trim() || null,
                    }),
                  )
                }
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
            void act(() => client.productFromOffer(offer.id, { base_unit_code: baseUnit }));
          }}
        >
          <label>
            Новый товар, учёт в{" "}
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
            Создать товар из предложения
          </button>
        </form>
      )}

      {offer.decisions.length ? (
        <>
          <h4>История решений</h4>
          <ol className="history meta">
            {offer.decisions.map((d) => (
              <li key={d.id}>
                {d.decision === "accept"
                  ? "Подтверждено"
                  : d.decision === "reject"
                    ? "Отклонено"
                    : "Отменено"}
                : {productName(d.product_id)} · {new Date(d.decided_at).toLocaleString("ru-RU")}
                {d.override ? " · вопреки конфликту" : ""}
                {d.reason ? ` · ${d.reason}` : ""}
              </li>
            ))}
          </ol>
        </>
      ) : null}
    </aside>
  );
}
