import { useState, type SyntheticEvent } from "react";

import type { ApiClient, Field, FieldRevision, Unit } from "../api/client";
import { REVIEW_STATUS_LABELS, fieldLabel, formatDecimal, valueText } from "../review/format";
import { canonicalDecimal } from "../review/lines";
import { ErrorNotice } from "./ErrorNotice";

interface Props {
  client: ApiClient;
  field: Field;
  units: Unit[];
  selected: boolean;
  onSelect: () => void;
  onChanged: (field: Field) => void;
}

type Draft = Record<string, string>;

function initialDraft(field: Field): Draft {
  const v = field.normalized_value ?? {};
  if (field.value_type === "money") return { amount: v.amount ?? "", currency: v.currency ?? "" };
  if (field.value_type === "quantity") return { value: v.value ?? "", unit: v.unit ?? "" };
  return { value: v.value ?? field.raw_value ?? "" };
}

/** Builds the API value or returns a user-facing problem. */
export function toValue(
  field: Field,
  draft: Draft,
): { value?: Record<string, string>; problem?: string } {
  if (field.value_type === "money") {
    const amount = canonicalDecimal(draft.amount ?? "");
    if (!amount) return { problem: "Сумма: число вида 1234,50 (без неоднозначных 1,234)." };
    const currency = (draft.currency ?? "").trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(currency))
      return { problem: "Валюта: три латинские буквы, например RUB." };
    return { value: { amount, currency } };
  }
  if (field.value_type === "quantity") {
    const value = canonicalDecimal(draft.value ?? "");
    if (!value) return { problem: "Количество: число вида 50 или 0,5." };
    if (!draft.unit) return { problem: "Выберите единицу." };
    return { value: { value, unit: draft.unit } };
  }
  if (field.value_type === "decimal") {
    const value = canonicalDecimal(draft.value ?? "");
    return value ? { value: { value } } : { problem: "Число вида 1234,5." };
  }
  const value = (draft.value ?? "").trim();
  return value ? { value: { value } } : { problem: "Значение не может быть пустым." };
}

export function FieldEditor({ client, field, units, selected, onSelect, onChanged }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => initialDraft(field));
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<FieldRevision[] | null>(null);
  const id = `field-${field.id}`;

  async function run(action: () => Promise<Field>) {
    setBusy(true);
    setError(null);
    try {
      const updated = await action();
      onChanged(updated);
      setEditing(false);
      setReason("");
      if (history) setHistory(await client.fieldRevisions(field.id));
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  function save(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const built = toValue(field, draft);
    if (!built.value) {
      setProblem(built.problem ?? null);
      return;
    }
    setProblem(null);
    const value = built.value;
    void run(() =>
      client.correctField(field.id, {
        value,
        reason: reason.trim() || null,
        expected_revision: field.revision,
      }),
    );
  }

  function decide(decision: "accept" | "reject") {
    void run(() =>
      client.decideField(field.id, {
        decision,
        reason: reason.trim() || null,
        expected_revision: field.revision,
      }),
    );
  }

  const set = (key: string) => (e: { target: { value: string } }) => {
    setDraft({ ...draft, [key]: e.target.value });
  };

  return (
    <li
      className={selected ? "field-card field-selected" : "field-card"}
      aria-labelledby={`${id}-label`}
    >
      <div className="field-head">
        <button type="button" className="link" id={`${id}-label`} onClick={onSelect}>
          {fieldLabel(field)}
        </button>
        <span className={`badge review-${field.review_status}`}>
          {REVIEW_STATUS_LABELS[field.review_status]}
        </span>
      </div>
      <dl className="field-values">
        <dt>В файле</dt>
        <dd>{field.raw_value ?? "—"}</dd>
        <dt>Значение</dt>
        <dd>{valueText(field, units)}</dd>
      </dl>
      {field.confidence !== null ? (
        <p className="meta">
          Оценка извлечения {formatDecimal(field.confidence.toFixed(2))} — это не подтверждение.
        </p>
      ) : null}
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      {editing ? (
        <form
          className="field-form"
          onSubmit={save}
          aria-label={`Исправление: ${fieldLabel(field)}`}
        >
          {field.value_type === "money" ? (
            <>
              <label>
                Сумма
                <input
                  value={draft.amount ?? ""}
                  onChange={set("amount")}
                  inputMode="decimal"
                  autoFocus
                />
              </label>
              <label>
                Валюта
                <input value={draft.currency ?? ""} onChange={set("currency")} maxLength={3} />
              </label>
            </>
          ) : field.value_type === "quantity" ? (
            <>
              <label>
                Количество
                <input
                  value={draft.value ?? ""}
                  onChange={set("value")}
                  inputMode="decimal"
                  autoFocus
                />
              </label>
              <label>
                Единица
                <select value={draft.unit ?? ""} onChange={set("unit")}>
                  <option value="">—</option>
                  {units.map((u) => (
                    <option key={u.code} value={u.code}>
                      {u.label_ru} ({u.code})
                    </option>
                  ))}
                </select>
              </label>
            </>
          ) : field.value_type === "unit" ? (
            <label>
              Единица
              <select value={draft.value ?? ""} onChange={set("value")} autoFocus>
                <option value="">—</option>
                {units.map((u) => (
                  <option key={u.code} value={u.code}>
                    {u.label_ru} ({u.code})
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label>
              Значение
              <input value={draft.value ?? ""} onChange={set("value")} autoFocus />
            </label>
          )}
          <label className="wide">
            Причина (сохраняется в истории)
            <input
              value={reason}
              maxLength={1000}
              onChange={(e) => {
                setReason(e.target.value);
              }}
            />
          </label>
          {problem ? (
            <p className="notice notice-error" role="alert">
              {problem}
            </p>
          ) : null}
          <div className="actions">
            <button type="submit" className="primary" disabled={busy}>
              Сохранить исправление
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setProblem(null);
                setDraft(initialDraft(field));
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") setEditing(false);
              }}
            >
              Отмена
            </button>
          </div>
        </form>
      ) : (
        <div className="actions">
          <button
            type="button"
            disabled={busy || field.normalized_value === null}
            title={field.normalized_value === null ? "Сначала исправьте значение" : undefined}
            onClick={() => {
              decide("accept");
            }}
          >
            Подтвердить
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setDraft(initialDraft(field));
              setEditing(true);
            }}
          >
            Исправить
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              decide("reject");
            }}
          >
            Отклонить
          </button>
          <button
            type="button"
            className="link"
            onClick={() => {
              void client.fieldRevisions(field.id).then(setHistory, setError);
            }}
          >
            История
          </button>
        </div>
      )}
      {history ? (
        history.length === 0 ? (
          <p className="meta">Правок не было.</p>
        ) : (
          <ol className="history">
            {history.map((h) => (
              <li key={h.revision}>
                r{h.revision}:{" "}
                {h.action === "correct"
                  ? "исправлено"
                  : h.action === "accept"
                    ? "подтверждено"
                    : "отклонено"}
                {h.action === "correct"
                  ? ` (${valueText({ value_type: field.value_type, normalized_value: h.previous_value }, units)} → ${valueText({ value_type: field.value_type, normalized_value: h.new_value }, units)})`
                  : ""}
                {h.reason ? `, причина: ${h.reason}` : ""}
                <span className="meta">
                  {" "}
                  {h.actor}, {new Date(h.occurred_at).toLocaleString("ru-RU")}
                </span>
              </li>
            ))}
          </ol>
        )
      ) : null}
    </li>
  );
}
