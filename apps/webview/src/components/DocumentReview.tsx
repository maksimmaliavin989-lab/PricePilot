import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { ApiClient, Field, Line, SourceDocument, Unit } from "../api/client";
import {
  DOCUMENT_STATUS_LABELS,
  isPriceKey,
  priceName,
  reasonLabel,
  valueText,
} from "../review/format";
import {
  LINE_STATE_LABELS,
  fieldOf,
  lineState,
  openIssues,
  moveSelection,
  visibleLines,
  withField,
  type LineFilter,
  type LineSort,
} from "../review/lines";
import { ErrorNotice } from "./ErrorNotice";
import { FieldEditor } from "./FieldEditor";
import { LineIssues } from "./LineIssues";
import { NextStep } from "./NextStep";
import { SourcePanel } from "./SourcePanel";

interface Props {
  client: ApiClient;
  document: SourceDocument;
  supplierName: string;
  onBack: () => void;
  /** Go to the next step of the path (matching this document's lines). */
  onNext?: (() => void) | undefined;
  onReviewed?: (() => void) | undefined;
}

function priceSummary(line: Line): string {
  const prices = line.fields.filter((f) => isPriceKey(f.field_key));
  if (prices.length === 0) return "—";
  return prices
    .map((f) => {
      const name = priceName(f.field_key);
      return name ? `${name} ${valueText(f)}` : valueText(f);
    })
    .join(" · ");
}

export function DocumentReview({
  client,
  document,
  supplierName,
  onBack,
  onNext,
  onReviewed,
}: Props) {
  const [bulk, setBulk] = useState<{ done: number; total: number } | null>(null);
  const [lines, setLines] = useState<Line[] | null>(null);
  const [units, setUnits] = useState<Unit[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [filter, setFilter] = useState<LineFilter>("all");
  const [sort, setSort] = useState<LineSort>("source");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [fieldId, setFieldId] = useState<string | null>(null);
  const tableRef = useRef<HTMLTableSectionElement>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([client.listAllLines(document.id), client.listUnits()]).then(
      ([loaded, unitList]) => {
        if (cancelled) return;
        setLines(loaded);
        setUnits(unitList);
        setSelectedId(loaded[0]?.id ?? null);
      },
      (e: unknown) => {
        if (!cancelled) setError(e);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [client, document.id]);

  const visible = useMemo(
    () => visibleLines(lines ?? [], { filter, sort, search }),
    [lines, filter, sort, search],
  );
  // Selection is kept by id, so sorting or filtering never loses the source context.
  const selected = lines?.find((l) => l.id === selectedId) ?? null;
  const selectedField: Field | null =
    selected?.fields.find((f) => f.id === fieldId) ??
    (selected ? (selected.fields.find((f) => isPriceKey(f.field_key)) ?? null) : null);
  const counts = useMemo(() => {
    const all = lines ?? [];
    return {
      attention: all.filter((l) => lineState(l) === "attention").length,
      checked: all.filter((l) => lineState(l) === "checked").length,
      pending: all.filter((l) => lineState(l) === "pending").length,
    };
  }, [lines]);

  /** Accepts every field of the lines that have no extraction remark and are unchecked.
   * An explicit action of the user (confirmed in a dialog); lines with remarks stay. */
  async function acceptClean() {
    const clean = (lines ?? []).filter((l) => lineState(l) === "pending");
    const fields = clean.flatMap((l) => l.fields.filter((f) => f.review_status === "unreviewed"));
    if (fields.length === 0) return;
    const ok = window.confirm(
      `Подтвердить ${String(clean.length)} строк без замечаний (${String(fields.length)} полей) как есть? ` +
        "Строки с замечаниями останутся на проверку.",
    );
    if (!ok) return;
    setError(null);
    setBulk({ done: 0, total: fields.length });
    try {
      for (const [index, f] of fields.entries()) {
        const updated = await client.decideField(f.id, {
          decision: "accept",
          reason: "подтверждено списком: строка без замечаний",
          expected_revision: f.revision,
        });
        setLines((prev) => (prev ? withField(prev, updated) : prev));
        setBulk({ done: index + 1, total: fields.length });
      }
      for (const line of clean) replaceLine(await client.getLine(line.id));
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBulk(null);
      onReviewed?.();
    }
  }

  function replaceLine(updated: Line) {
    setLines((prev) => (prev ? prev.map((l) => (l.id === updated.id ? updated : l)) : prev));
  }

  function select(id: string | null) {
    setSelectedId(id);
    setFieldId(null);
    if (id) {
      window.requestAnimationFrame(() => {
        tableRef.current?.querySelector<HTMLElement>(`[data-line="${id}"]`)?.focus();
      });
    }
  }

  function onKey(event: KeyboardEvent<HTMLTableSectionElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      select(moveSelection(visible, selectedId, event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter" && selected) {
      event.preventDefault();
      window.document.getElementById("field-list")?.querySelector<HTMLElement>("button")?.focus();
    }
  }

  return (
    <section aria-labelledby="review-title">
      <header className="page-head">
        <div>
          <span className="eyebrow">
            Шаг 2 из 4 · {document.original_name} · {supplierName}
          </span>
          <h1 id="review-title">Проверьте, что программа прочитала верно</h1>
          <p className="lede">
            Справа — строка из исходного файла. Подтвердите значение или исправьте его.{" "}
            <span className={`badge status-${document.status}`}>
              {DOCUMENT_STATUS_LABELS[document.status]}
            </span>
          </p>
        </div>
        <button type="button" onClick={onBack}>
          ← Все прайсы
        </button>
      </header>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      {lines === null ? (
        <p className="muted">Загрузка строк…</p>
      ) : (
        <>
          <section className="card" aria-label="Прогресс проверки">
            <div className="progress" aria-hidden="true">
              <span className="p-ok" style={{ flex: counts.checked }} />
              <span className="p-warn" style={{ flex: counts.attention }} />
              <span className="p-todo" style={{ flex: counts.pending }} />
            </div>
            <p className="legend" role="status" style={{ margin: "10px 0 0" }}>
              <span>
                <i style={{ background: "var(--ok)" }} />
                Проверено {counts.checked} из {lines.length}
              </span>
              <span>
                <i style={{ background: "var(--warn)" }} />с замечаниями {counts.attention}
              </span>
              <span>
                <i style={{ background: "var(--border-strong)" }} />
                не проверено {counts.pending}
              </span>
              {bulk ? (
                <span>
                  подтверждение: {bulk.done} из {bulk.total}
                </span>
              ) : null}
            </p>
          </section>
          {counts.attention > 0 ? (
            <NextStep
              title={`разберите строки с замечаниями (${String(counts.attention)})`}
              action={{
                label: "Показать строки с замечаниями",
                onClick: () => {
                  setFilter("attention");
                },
              }}
              secondary={
                counts.pending > 0
                  ? {
                      label: `Подтвердить строки без замечаний (${String(counts.pending)})`,
                      onClick: () => void acceptClean(),
                      disabled: bulk !== null,
                    }
                  : undefined
              }
            >
              Выберите строку, сверьте поля с оригиналом справа и подтвердите или исправьте их.
            </NextStep>
          ) : counts.pending > 0 ? (
            <NextStep
              title={`подтвердите строки (${String(counts.pending)} без замечаний)`}
              action={{
                label: `Подтвердить строки без замечаний (${String(counts.pending)})`,
                onClick: () => void acceptClean(),
                disabled: bulk !== null,
              }}
              secondary={onNext ? { label: "Перейти к сопоставлению", onClick: onNext } : undefined}
            >
              Сверьте несколько строк с оригиналом; если всё верно — подтвердите остальные списком.
            </NextStep>
          ) : (
            <NextStep
              title="сопоставьте строки с товарами каталога"
              action={onNext ? { label: "Перейти к сопоставлению", onClick: onNext } : undefined}
            >
              Все строки проверены.
            </NextStep>
          )}
          <div className="toolbar" role="group" aria-label="Фильтр и сортировка строк">
            <div className="chips" role="group" aria-label="Показать">
              {(
                [
                  ["attention", "С замечаниями", counts.attention],
                  ["pending", "Не проверено", counts.pending],
                  ["checked", "Проверено", counts.checked],
                  ["all", "Все строки", lines.length],
                ] as const
              ).map(([value, label, count]) => (
                <button
                  key={value}
                  type="button"
                  className="chip"
                  aria-pressed={filter === value}
                  onClick={() => {
                    setFilter(value);
                  }}
                >
                  {label} · {count}
                </button>
              ))}
            </div>
            <label>
              Сортировка
              <select
                value={sort}
                onChange={(e) => {
                  setSort(e.target.value as LineSort);
                }}
              >
                <option value="source">Как в файле</option>
                <option value="name">По наименованию</option>
              </select>
            </label>
            <label>
              Поиск
              <input
                type="search"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                }}
              />
            </label>
            <span className="meta">↑/↓ — выбор строки, Enter — к полям</span>
          </div>
          <div className="review-layout">
            <div className="review-table">
              <table aria-label="Извлечённые строки">
                <thead>
                  <tr>
                    <th scope="col">Стр.</th>
                    <th scope="col">Наименование</th>
                    <th scope="col">Ед.</th>
                    <th scope="col">Цена</th>
                    <th scope="col">Проверка</th>
                  </tr>
                </thead>
                <tbody ref={tableRef} onKeyDown={onKey}>
                  {visible.map((line) => {
                    const state = lineState(line);
                    const isSelected = line.id === selectedId;
                    const unit = fieldOf(line, "unit");
                    return (
                      <tr
                        key={line.id}
                        data-line={line.id}
                        tabIndex={isSelected || (!selectedId && line === visible[0]) ? 0 : -1}
                        aria-selected={isSelected}
                        className={isSelected ? "row-selected" : undefined}
                        onClick={() => {
                          select(line.id);
                        }}
                        onFocus={() => {
                          if (!isSelected) setSelectedId(line.id);
                        }}
                      >
                        <td>{line.source.row ?? line.ordinal}</td>
                        <td>{fieldOf(line, "name")?.raw_value ?? line.raw_text}</td>
                        <td>
                          {unit
                            ? unit.normalized_value
                              ? valueText(unit, units)
                              : `? ${unit.raw_value ?? ""}`
                            : "—"}
                        </td>
                        <td>{priceSummary(line)}</td>
                        <td>
                          <span className={`badge line-${state}`}>{LINE_STATE_LABELS[state]}</span>
                          {openIssues(line).map((issue) => (
                            <div key={issue.code} className="meta">
                              {reasonLabel(issue.code)}
                            </div>
                          ))}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {visible.length === 0 ? <p className="muted">Нет строк под этот фильтр.</p> : null}
            </div>
            {selected ? (
              <aside className="review-side" aria-label="Выбранная строка">
                <SourcePanel
                  client={client}
                  documentId={document.id}
                  line={selected}
                  field={selectedField}
                  hiddenByFilter={!visible.some((l) => l.id === selected.id)}
                />
                <section aria-labelledby="fields-title" className="card">
                  <h3 id="fields-title">Поля строки {selected.source.row ?? selected.ordinal}</h3>
                  <LineIssues
                    client={client}
                    line={selected}
                    units={units}
                    onSelectField={(id) => {
                      setFieldId(id);
                      window.requestAnimationFrame(() => {
                        window.document
                          .querySelector<HTMLElement>(`[data-field="${id}"]`)
                          ?.scrollIntoView({ block: "nearest" });
                      });
                    }}
                    onLineChanged={(updated) => {
                      replaceLine(updated);
                      onReviewed?.();
                    }}
                  />
                  <ul className="list" id="field-list">
                    {selected.fields.map((f) => (
                      <FieldEditor
                        key={`${f.id}:${String(f.revision)}`}
                        client={client}
                        field={f}
                        units={units}
                        selected={f.id === selectedField?.id}
                        onSelect={() => {
                          setFieldId(f.id);
                        }}
                        onChanged={(updated) => {
                          onReviewed?.();
                          setLines((prev) => (prev ? withField(prev, updated) : prev));
                          // Remarks are derived by the server from all fields of the line.
                          client.getLine(updated.line_id).then(replaceLine, setError);
                          setFieldId(updated.id);
                        }}
                      />
                    ))}
                  </ul>
                </section>
              </aside>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}
