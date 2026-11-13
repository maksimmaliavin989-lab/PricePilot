import { useEffect, useState } from "react";

import type {
  ApiClient,
  ComponentType,
  CostPolicy,
  FxRate,
  TaxRateSource,
  Workspace,
} from "../api/client";
import { BASE_LABELS, BASIS_LABELS, CATEGORY_LABELS } from "../review/costs";
import { formatDecimal } from "../review/format";
import { ErrorNotice } from "./ErrorNotice";

interface Props {
  client: ApiClient;
  workspace: Workspace;
}

const EMPTY_TYPE: ComponentType = {
  code: "",
  label: "",
  category: "charge",
  basis: "per_order",
  base: "goods_net",
  required: false,
  default_value: null,
  default_currency: null,
};

export function CostSettings({ client, workspace }: Props) {
  const [policy, setPolicy] = useState<CostPolicy | null>(null);
  const [types, setTypes] = useState<ComponentType[]>([]);
  const [draft, setDraft] = useState<ComponentType>(EMPTY_TYPE);
  const [fx, setFx] = useState<FxRate[]>([]);
  const [sources, setSources] = useState<TaxRateSource[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const state = { cancelled: false };
    void (async () => {
      try {
        const [p, rates, taxes] = await Promise.all([
          client.costPolicy(workspace.id),
          client.listFxRates(workspace.id),
          client.listTaxRateSources(workspace.id),
        ]);
        if (state.cancelled) return;
        setPolicy(p);
        setTypes(p.types);
        setFx(rates);
        setSources(taxes);
      } catch (e: unknown) {
        if (!state.cancelled) setError(e);
      }
    })();
    return () => {
      state.cancelled = true;
    };
  }, [client, workspace.id, tick]);

  async function savePolicy() {
    if (!policy) return;
    setError(null);
    setSaved(false);
    try {
      const next = await client.saveCostPolicy(workspace.id, {
        name: policy.name,
        types,
        expected_version: policy.version,
      });
      setPolicy(next);
      setTypes(next.types);
      setSaved(true);
    } catch (e: unknown) {
      setError(e);
    }
  }

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= types.length) return;
    const next = [...types];
    const [item] = next.splice(index, 1);
    if (item) next.splice(target, 0, item);
    setTypes(next);
  }

  const taxLike = draft.category === "tax" || draft.category === "duty";

  return (
    <div className="card">
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      <h3>Компоненты полной стоимости</h3>
      <p className="muted">
        Порядок строк — порядок расчёта. Налоги и пошлины не имеют значений по умолчанию: они
        учитываются только при явном вводе с источником или из проверенного источника ставок.
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col">Компонент</th>
            <th scope="col">Вид</th>
            <th scope="col">Как считается</th>
            <th scope="col">Обязателен</th>
            <th scope="col">По умолчанию</th>
            <th scope="col">
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {types.map((t, i) => (
            <tr key={t.code}>
              <td>
                {t.label}
                <div className="meta">{t.code}</div>
              </td>
              <td>{CATEGORY_LABELS[t.category]}</td>
              <td>
                {BASIS_LABELS[t.basis]}
                {t.basis === "percent" ? ` ${BASE_LABELS[t.base]}` : ""}
              </td>
              <td>
                <input
                  type="checkbox"
                  aria-label={`${t.label}: обязателен`}
                  checked={t.required}
                  onChange={(e) => {
                    setTypes(
                      types.map((x, j) => (j === i ? { ...x, required: e.target.checked } : x)),
                    );
                  }}
                />
              </td>
              <td>
                {t.default_value
                  ? `${formatDecimal(t.default_value)} ${t.default_currency ?? "%"}`
                  : "—"}
              </td>
              <td className="actions">
                <button
                  type="button"
                  aria-label={`${t.label}: выше`}
                  onClick={() => {
                    move(i, -1);
                  }}
                >
                  ↑
                </button>
                <button
                  type="button"
                  aria-label={`${t.label}: ниже`}
                  onClick={() => {
                    move(i, 1);
                  }}
                >
                  ↓
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setTypes(types.filter((_, j) => j !== i));
                  }}
                >
                  Убрать
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        className="form-grid"
        aria-label="Новый компонент"
        onSubmit={(e) => {
          e.preventDefault();
          setTypes([
            ...types,
            {
              ...draft,
              code: draft.code.trim(),
              label: draft.label.trim(),
              default_value: taxLike ? null : draft.default_value?.trim() || null,
              default_currency:
                taxLike || draft.basis === "percent" || !draft.default_value?.trim()
                  ? null
                  : (draft.default_currency?.trim().toUpperCase() ?? null),
            },
          ]);
          setDraft(EMPTY_TYPE);
        }}
      >
        <label>
          Название
          <input
            value={draft.label}
            required
            maxLength={100}
            placeholder="Разгрузка"
            onChange={(e) => {
              setDraft({ ...draft, label: e.target.value });
            }}
          />
        </label>
        <label>
          Код (латиницей)
          <input
            value={draft.code}
            required
            pattern="[a-z][a-z0-9_]{1,39}"
            placeholder="unloading"
            onChange={(e) => {
              setDraft({ ...draft, code: e.target.value });
            }}
          />
        </label>
        <label>
          Вид
          <select
            value={draft.category}
            onChange={(e) => {
              setDraft({ ...draft, category: e.target.value as ComponentType["category"] });
            }}
          >
            {Object.entries(CATEGORY_LABELS).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label>
          Как считается
          <select
            value={draft.basis}
            onChange={(e) => {
              setDraft({ ...draft, basis: e.target.value as ComponentType["basis"] });
            }}
          >
            {Object.entries(BASIS_LABELS).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        {draft.basis === "percent" ? (
          <label>
            База процента
            <select
              value={draft.base}
              onChange={(e) => {
                setDraft({ ...draft, base: e.target.value as ComponentType["base"] });
              }}
            >
              {Object.entries(BASE_LABELS).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {taxLike ? null : (
          <label>
            Значение по умолчанию (необязательно)
            <input
              value={draft.default_value ?? ""}
              inputMode="decimal"
              onChange={(e) => {
                setDraft({ ...draft, default_value: e.target.value });
              }}
            />
          </label>
        )}
        {taxLike || draft.basis === "percent" ? null : (
          <label>
            Валюта значения
            <input
              value={draft.default_currency ?? ""}
              maxLength={3}
              onChange={(e) => {
                setDraft({ ...draft, default_currency: e.target.value });
              }}
            />
          </label>
        )}
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.required}
            onChange={(e) => {
              setDraft({ ...draft, required: e.target.checked });
            }}
          />
          Обязателен в каждом расчёте
        </label>
        <div className="actions">
          <button type="submit">Добавить компонент</button>
          <button type="button" className="primary" onClick={() => void savePolicy()}>
            Сохранить компоненты
          </button>
          {saved ? <span role="status">Сохранено.</span> : null}
        </div>
      </form>

      <FxRates
        client={client}
        workspace={workspace}
        rates={fx}
        onAdded={() => {
          setTick((t) => t + 1);
        }}
        onError={setError}
      />
      <TaxSources
        client={client}
        workspace={workspace}
        types={types.filter((t) => t.category === "tax" || t.category === "duty")}
        sources={sources}
        onAdded={() => {
          setTick((t) => t + 1);
        }}
        onError={setError}
      />
    </div>
  );
}

function FxRates({
  client,
  workspace,
  rates,
  onAdded,
  onError,
}: {
  client: ApiClient;
  workspace: Workspace;
  rates: FxRate[];
  onAdded: () => void;
  onError: (e: unknown) => void;
}) {
  const [form, setForm] = useState({
    base: "USD",
    quote: workspace.base_currency,
    rate: "",
    effective_date: new Date().toISOString().slice(0, 10),
    source: "",
  });
  return (
    <>
      <h3>Курсы валют</h3>
      <p className="muted">Курс вводится вручную с датой и источником; сеть не используется.</p>
      {rates.length ? (
        <ul className="meta">
          {rates.map((r) => (
            <li key={r.id}>
              1 {r.base} = {formatDecimal(r.rate)} {r.quote} на {r.effective_date} · {r.source}
            </li>
          ))}
        </ul>
      ) : null}
      <form
        className="inline"
        aria-label="Новый курс"
        onSubmit={(e) => {
          e.preventDefault();
          client
            .addFxRate(workspace.id, {
              ...form,
              base: form.base.toUpperCase(),
              quote: form.quote.toUpperCase(),
              rate: form.rate.trim().replace(",", "."),
            })
            .then(() => {
              setForm({ ...form, rate: "", source: "" });
              onAdded();
            })
            .catch(onError);
        }}
      >
        1{" "}
        <input
          aria-label="Валюта"
          value={form.base}
          size={4}
          maxLength={3}
          onChange={(e) => {
            setForm({ ...form, base: e.target.value });
          }}
        />{" "}
        ={" "}
        <input
          aria-label="Курс"
          value={form.rate}
          size={8}
          inputMode="decimal"
          onChange={(e) => {
            setForm({ ...form, rate: e.target.value });
          }}
        />{" "}
        <input
          aria-label="В валюте"
          value={form.quote}
          size={4}
          maxLength={3}
          onChange={(e) => {
            setForm({ ...form, quote: e.target.value });
          }}
        />
        <input
          aria-label="Дата курса"
          type="date"
          value={form.effective_date}
          onChange={(e) => {
            setForm({ ...form, effective_date: e.target.value });
          }}
        />
        <input
          aria-label="Источник курса"
          placeholder="источник, например ЦБ РФ"
          value={form.source}
          onChange={(e) => {
            setForm({ ...form, source: e.target.value });
          }}
        />
        <button type="submit" disabled={!form.rate.trim() || !form.source.trim()}>
          Добавить курс
        </button>
      </form>
    </>
  );
}

function TaxSources({
  client,
  workspace,
  types,
  sources,
  onAdded,
  onError,
}: {
  client: ApiClient;
  workspace: Workspace;
  types: ComponentType[];
  sources: TaxRateSource[];
  onAdded: () => void;
  onError: (e: unknown) => void;
}) {
  const [form, setForm] = useState({
    name: "",
    reference: "",
    verified_by: "",
    code: types[0]?.code ?? "vat",
    rate: "",
    valid_from: "",
    valid_to: "",
  });
  return (
    <>
      <h3>Проверенные источники ставок</h3>
      <p className="muted">
        Ставка из источника применяется автоматически только в срок действия; иначе налог в расчёте
        — «неизвестно». Укажите документ и кто его проверил.
      </p>
      {sources.length ? (
        <ul className="meta">
          {sources.map((s) => (
            <li key={s.id}>
              {s.name} · {s.reference} · проверил {s.verified_by}:{" "}
              {s.rates
                .map(
                  (r) =>
                    `${r.component_code} ${formatDecimal(r.rate)}% с ${r.valid_from}${r.valid_to ? ` по ${r.valid_to}` : ""}`,
                )
                .join("; ")}
            </li>
          ))}
        </ul>
      ) : null}
      <form
        className="form-grid"
        aria-label="Новый источник ставок"
        onSubmit={(e) => {
          e.preventDefault();
          client
            .addTaxRateSource(workspace.id, {
              name: form.name,
              reference: form.reference,
              verified_by: form.verified_by,
              verified_at: new Date().toISOString(),
              rates: [
                {
                  component_code: form.code,
                  rate: form.rate.trim().replace(",", "."),
                  valid_from: form.valid_from,
                  valid_to: form.valid_to || null,
                  scope: null,
                },
              ],
            })
            .then(() => {
              setForm({ ...form, rate: "", reference: "" });
              onAdded();
            })
            .catch(onError);
        }}
      >
        <label>
          Название
          <input
            value={form.name}
            required
            onChange={(e) => {
              setForm({ ...form, name: e.target.value });
            }}
          />
        </label>
        <label>
          Документ
          <input
            value={form.reference}
            required
            placeholder="приказ, публикация, номер"
            onChange={(e) => {
              setForm({ ...form, reference: e.target.value });
            }}
          />
        </label>
        <label>
          Кто проверил
          <input
            value={form.verified_by}
            required
            onChange={(e) => {
              setForm({ ...form, verified_by: e.target.value });
            }}
          />
        </label>
        <label>
          Компонент
          <select
            value={form.code}
            onChange={(e) => {
              setForm({ ...form, code: e.target.value });
            }}
          >
            {types.map((t) => (
              <option key={t.code} value={t.code}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Ставка, %
          <input
            value={form.rate}
            required
            inputMode="decimal"
            onChange={(e) => {
              setForm({ ...form, rate: e.target.value });
            }}
          />
        </label>
        <label>
          Действует с
          <input
            type="date"
            value={form.valid_from}
            required
            onChange={(e) => {
              setForm({ ...form, valid_from: e.target.value });
            }}
          />
        </label>
        <label>
          по (необязательно)
          <input
            type="date"
            value={form.valid_to}
            onChange={(e) => {
              setForm({ ...form, valid_to: e.target.value });
            }}
          />
        </label>
        <div className="actions">
          <button type="submit" disabled={types.length === 0}>
            Добавить источник
          </button>
        </div>
      </form>
    </>
  );
}
