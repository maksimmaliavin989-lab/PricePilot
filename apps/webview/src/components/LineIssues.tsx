import { useState } from "react";

import type { ApiClient, Line, LineIssue, Unit } from "../api/client";
import { canonicalDecimal } from "../review/lines";
import { reasonLabel } from "../review/format";
import { ErrorNotice } from "./ErrorNotice";

interface Props {
  client: ApiClient;
  line: Line;
  units: readonly Unit[];
  /** Select (and scroll to) the field a remark concerns. */
  onSelectField: (fieldId: string) => void;
  onLineChanged: (line: Line) => void;
}

const FIELD_NAMES: Record<string, string> = {
  unit: "Единица",
  price: "Цена",
  name: "Наименование",
};

const ACTION_LABELS: Record<LineIssue["action"], string> = {
  add_value: "Добавить значение",
  fix_value: "Исправить значение",
  confirm_or_fix: "Подтвердить или заменить",
  choose_price: "Выбрать цену",
  confirm: "Проверить и подтвердить",
};

/** Remarks of a line, each tied to a field and to the action that resolves it. */
export function LineIssues({ client, line, units, onSelectField, onLineChanged }: Props) {
  const open = line.issues.filter((i) => !i.resolved);
  const resolved = line.issues.filter((i) => i.resolved);
  if (line.issues.length === 0) return null;
  return (
    <section aria-label="Замечания строки" className="issues">
      {open.length ? (
        <ol className="issue-list">
          {open.map((issue) => (
            <li key={issue.code} className="issue">
              <strong>{reasonLabel(issue.code)}</strong>
              <div>{issue.hint}</div>
              {issue.field_ids.length ? (
                <button
                  type="button"
                  className="primary"
                  onClick={() => {
                    const first = issue.field_ids[0];
                    if (first) onSelectField(first);
                  }}
                >
                  {ACTION_LABELS[issue.action]}: поле «
                  {FIELD_NAMES[issue.field_key] ?? issue.field_key}»
                </button>
              ) : (
                <AddField
                  client={client}
                  line={line}
                  issue={issue}
                  units={units}
                  onLineChanged={onLineChanged}
                />
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p className="notice" role="status">
          Все замечания по строке сняты.
        </p>
      )}
      {resolved.length ? (
        <details>
          <summary>Снятые замечания ({resolved.length})</summary>
          <ul className="meta">
            {resolved.map((issue) => (
              <li key={issue.code}>✓ {reasonLabel(issue.code)}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function AddField({
  client,
  line,
  issue,
  units,
  onLineChanged,
}: {
  client: ApiClient;
  line: Line;
  issue: LineIssue;
  units: readonly Unit[];
  onLineChanged: (line: Line) => void;
}) {
  const [unit, setUnit] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("RUB");
  const [error, setError] = useState<unknown>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isUnit = issue.field_key === "unit";

  async function submit() {
    setProblem(null);
    setError(null);
    let value: Record<string, string>;
    if (isUnit) {
      if (!unit) {
        setProblem("Выберите единицу.");
        return;
      }
      value = { value: unit };
    } else {
      const canonical = canonicalDecimal(amount);
      if (!canonical) {
        setProblem("Укажите цену числом, например 1 250,50.");
        return;
      }
      value = { amount: canonical, currency: currency.trim().toUpperCase() };
    }
    setBusy(true);
    try {
      onLineChanged(
        await client.addLineField(line.id, {
          field_key: isUnit ? "unit" : "price",
          value,
          reason: "в файле нет значения — указано при проверке",
        }),
      );
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="inline"
      aria-label={isUnit ? "Добавить единицу" : "Добавить цену"}
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      {isUnit ? (
        <select
          aria-label="Единица"
          value={unit}
          onChange={(e) => {
            setUnit(e.target.value);
          }}
        >
          <option value="">— выберите —</option>
          {units.map((u) => (
            <option key={u.code} value={u.code}>
              {u.label_ru}
            </option>
          ))}
        </select>
      ) : (
        <>
          <input
            aria-label="Цена"
            value={amount}
            inputMode="decimal"
            size={10}
            onChange={(e) => {
              setAmount(e.target.value);
            }}
          />
          <input
            aria-label="Валюта цены"
            value={currency}
            maxLength={3}
            size={4}
            onChange={(e) => {
              setCurrency(e.target.value);
            }}
          />
        </>
      )}
      <button type="submit" className="primary" disabled={busy}>
        {isUnit ? "Добавить единицу" : "Добавить цену"}
      </button>
      {problem ? (
        <span className="meta" role="alert">
          {problem}
        </span>
      ) : null}
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
    </form>
  );
}
