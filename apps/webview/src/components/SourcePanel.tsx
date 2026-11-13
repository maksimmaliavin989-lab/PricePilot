import { useEffect, useState } from "react";

import type { ApiClient, Field, Line, SourceWindow } from "../api/client";
import { describeError } from "./ErrorNotice";

interface Props {
  client: ApiClient;
  documentId: string;
  line: Line;
  field: Field | null;
  hiddenByFilter: boolean;
}

const CONTEXT_ROWS = 5;

const LOCALIZATION_LABELS = {
  exact: "точное место (ячейка)",
  approximate: "строка без ячейки",
  none: "место в файле неизвестно",
} as const;

/** Read-only view of the original around the selected line: the line's row and the selected
 * field's cell are highlighted, and the highlight is also stated in text. */
export function SourcePanel({ client, documentId, line, field, hiddenByFilter }: Props) {
  const [view, setView] = useState<SourceWindow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const row = line.source.row;
  const sheet = line.source.sheet;

  useEffect(() => {
    if (row === null) return undefined;
    let cancelled = false;
    client
      .sourceRows(documentId, {
        sheet,
        from: Math.max(1, row - CONTEXT_ROWS),
        to: row + CONTEXT_ROWS,
      })
      .then((w) => {
        if (!cancelled) {
          setView(w);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(describeError(e).message);
      });
    return () => {
      cancelled = true;
    };
  }, [client, documentId, sheet, row]);

  const cellRef = field?.provenance.cell ?? null;
  const columns = view
    ? Array.from(new Set(view.rows.flatMap((r) => r.cells.map((c) => c.column)))).sort(
        (a, b) => a - b,
      )
    : [];

  return (
    <section aria-labelledby="source-title" className="card source-panel">
      <h3 id="source-title">Оригинал</h3>
      <p className="meta">
        {sheet ? `Лист «${sheet}», ` : ""}строка {row ?? "—"}
        {cellRef ? `, ячейка ${cellRef}` : ""}
        {field ? ` · ${LOCALIZATION_LABELS[field.provenance.localization]}` : ""}
      </p>
      {hiddenByFilter ? (
        <p className="notice">Строка скрыта текущим фильтром, но остаётся выбранной.</p>
      ) : null}
      {error ? <p className="notice notice-error">{error}</p> : null}
      {view && row !== null ? (
        <div className="source-scroll">
          <table className="source-table" aria-label="Фрагмент исходного файла">
            <tbody>
              {view.rows.map((r) => {
                const current = r.index === row;
                return (
                  <tr key={r.index} className={current ? "source-current" : undefined}>
                    <th scope="row">
                      {r.index}
                      {current ? <span className="sr-only"> (выбранная строка)</span> : null}
                    </th>
                    {columns.map((col) => {
                      const cell = r.cells.find((c) => c.column === col);
                      const selected = current && cell?.ref === cellRef;
                      return (
                        <td
                          key={col}
                          className={selected ? "source-cell-selected" : undefined}
                          title={cell?.formula ? `Формула: ${cell.formula}` : cell?.ref}
                        >
                          {cell?.text ?? ""}
                          {selected ? <span className="badge">поле</span> : null}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      <details>
        <summary>Исходный текст строки</summary>
        <pre className="raw-text">{line.raw_text}</pre>
      </details>
    </section>
  );
}
