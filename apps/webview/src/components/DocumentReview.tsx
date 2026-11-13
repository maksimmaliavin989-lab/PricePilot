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
  moveSelection,
  visibleLines,
  withField,
  type LineFilter,
  type LineSort,
} from "../review/lines";
import { ErrorNotice } from "./ErrorNotice";
import { FieldEditor } from "./FieldEditor";
import { SourcePanel } from "./SourcePanel";

interface Props {
  client: ApiClient;
  document: SourceDocument;
  supplierName: string;
  onBack: () => void;
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

export function DocumentReview({ client, document, supplierName, onBack }: Props) {
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
    };
  }, [lines]);

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
      <div className="toolbar">
        <button type="button" onClick={onBack}>
          ← К документам
        </button>
        <h2 id="review-title">{document.original_name}</h2>
        <span className={`badge status-${document.status}`}>
          {DOCUMENT_STATUS_LABELS[document.status]}
        </span>
        <span className="meta">{supplierName}</span>
      </div>
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
          <div className="toolbar" role="group" aria-label="Фильтр и сортировка строк">
            <label>
              Показать
              <select
                value={filter}
                onChange={(e) => {
                  setFilter(e.target.value as LineFilter);
                }}
              >
                <option value="all">Все строки ({lines.length})</option>
                <option value="attention">Требуют внимания ({counts.attention})</option>
                <option value="checked">Проверенные ({counts.checked})</option>
              </select>
            </label>
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
                          {(state === "checked" ? [] : line.review_reasons).map((r) => (
                            <div key={r} className="meta">
                              {reasonLabel(r)}
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
                  {selected.review_reasons.length ? (
                    <ul
                      className="reasons"
                      aria-label={
                        lineState(selected) === "checked"
                          ? "Замечания извлечения (уже разрешены проверкой)"
                          : "Замечания извлечения"
                      }
                    >
                      {selected.review_reasons.map((r) => (
                        <li key={r}>{reasonLabel(r)}</li>
                      ))}
                    </ul>
                  ) : null}
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
                          setLines((prev) => (prev ? withField(prev, updated) : prev));
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
