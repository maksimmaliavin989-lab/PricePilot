import { useCallback, useEffect, useState } from "react";

import type { ApiClient, ProcessingRun, SourceDocument, Supplier, Workspace } from "../api/client";
import {
  ACTIVE_RUN,
  DOCUMENT_STATUS_LABELS,
  RUN_STATUS_LABELS,
  errorLabel,
} from "../review/format";
import { DocumentReview } from "./DocumentReview";
import { ErrorNotice } from "./ErrorNotice";
import { ImportPanel } from "./ImportPanel";
import { NextStep } from "./NextStep";
import { reviewCounts } from "../journey/progress";
import type { StepId } from "../journey/progress";

interface Props {
  client: ApiClient;
  workspace: Workspace;
  onGo?: (step: StepId, documentId?: string) => void;
  onProgress?: () => void;
}

const POLL_MS = 1000;

async function loadAll(
  client: ApiClient,
  workspaceId: string,
): Promise<{
  documents: SourceDocument[];
  runs: Record<string, ProcessingRun>;
  review: Record<string, ReviewState>;
}> {
  const documents: SourceDocument[] = [];
  let cursor: string | undefined;
  do {
    const page = await client.listDocuments(workspaceId, cursor);
    documents.push(...page.items);
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
  documents.reverse(); // newest first
  const runs: Record<string, ProcessingRun> = {};
  await Promise.all(
    documents
      .filter((d) => d.latest_run_id)
      .map(async (d) => {
        runs[d.id] = await client.getRun(d.latest_run_id ?? "");
      }),
  );
  // Review state per processed document, from its lines (the same rule as the review screen).
  const review: Record<string, ReviewState> = {};
  await Promise.all(
    documents
      .filter((d) => {
        const run = runs[d.id];
        return run && !ACTIVE_RUN.has(run.status) && run.counts.lines > 0;
      })
      .slice(0, 20)
      .map(async (d) => {
        const counts = reviewCounts(await client.listAllLines(d.id));
        review[d.id] = { ...counts, total: counts.checked + counts.attention + counts.pending };
      }),
  );
  return { documents, runs, review };
}

interface ReviewState {
  checked: number;
  attention: number;
  pending: number;
  total: number;
}

function stageText(doc: SourceDocument, run: ProcessingRun | undefined): string {
  if (!run) return DOCUMENT_STATUS_LABELS[doc.status];
  const stage = RUN_STATUS_LABELS[run.status];
  if (run.error) return `${stage}: ${errorLabel(run.error.code)}`;
  if (run.progress?.total) {
    return `${stage} (${String(run.progress.completed)} из ${String(run.progress.total)})`;
  }
  return stage;
}

export function DocumentsView({ client, workspace, onGo, onProgress }: Props) {
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [runs, setRuns] = useState<Record<string, ProcessingRun>>({});
  const [review, setReview] = useState<Record<string, ReviewState>>({});
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => {
    setTick((t) => t + 1);
  }, []);

  useEffect(() => {
    const state = { cancelled: false };
    void (async () => {
      try {
        const [loaded, supplierPage] = await Promise.all([
          loadAll(client, workspace.id),
          client.listSuppliers({ workspaceId: workspace.id, includeArchived: true }),
        ]);
        if (state.cancelled) return;
        setDocuments(loaded.documents);
        setRuns(loaded.runs);
        setReview(loaded.review);
        setSuppliers(supplierPage.items);
      } catch (e: unknown) {
        if (!state.cancelled) setError(e);
      }
    })();
    return () => {
      state.cancelled = true;
    };
  }, [client, workspace.id, tick]);

  const anyActive = Object.values(runs).some((r) => ACTIVE_RUN.has(r.status));
  useEffect(() => {
    if (!anyActive) return undefined;
    const timer = setTimeout(refresh, POLL_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [anyActive, tick, refresh]);

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
    } catch (e: unknown) {
      setError(e);
    }
    refresh();
  }

  const supplierName = (id: string | null) =>
    suppliers.find((s) => s.id === id)?.name ?? "Поставщик не указан";
  const open = documents.find((d) => d.id === openId);

  if (open) {
    return (
      <DocumentReview
        client={client}
        document={open}
        supplierName={supplierName(open.supplier_id)}
        onBack={() => {
          setOpenId(null);
          refresh();
          onProgress?.();
        }}
        onNext={() => {
          onGo?.("match", open.id);
        }}
        onReviewed={onProgress}
      />
    );
  }

  return (
    <section aria-labelledby="docs-title">
      <h2 id="docs-title">Документы · {workspace.name}</h2>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      {(() => {
        const ready = documents.filter((d) => (runs[d.id]?.counts.lines ?? 0) > 0);
        if (documents.length === 0) {
          return (
            <NextStep title="загрузите прайс поставщика">
              Выберите поставщика и перетащите файл (XLSX, XLS, CSV, DOCX, PDF, JPG, PNG) в поле
              выше. Оригинал сохраняется без изменений.
            </NextStep>
          );
        }
        if (anyActive && ready.length === 0) {
          return <NextStep title="дождитесь окончания обработки">Статус обновляется сам.</NextStep>;
        }
        const first =
          ready.find((d) => {
            const r = review[d.id];
            return !r || r.checked < r.total;
          }) ?? null;
        const reviewed = ready[0];
        if (!reviewed) {
          return (
            <NextStep title="загрузите другой файл">
              Ни один документ не дал строк прайса — причина указана в столбце «Этап».
            </NextStep>
          );
        }
        if (!first) {
          return (
            <NextStep
              title="сопоставьте строки с товарами каталога"
              action={{
                label: "Перейти к сопоставлению",
                onClick: () => onGo?.("match", reviewed.id),
              }}
            >
              Все строки обработанных прайсов проверены. Новый прайс можно загрузить ниже.
            </NextStep>
          );
        }
        const state = review[first.id];
        return (
          <NextStep
            title={`проверьте строки «${first.original_name}»${
              state ? ` (проверено ${String(state.checked)} из ${String(state.total)})` : ""
            }`}
            action={{
              label: "Проверить строки",
              onClick: () => {
                setOpenId(first.id);
              },
            }}
            secondary={{
              label: "Сразу к сопоставлению",
              onClick: () => onGo?.("match", first.id),
            }}
          >
            Проверка подтверждает цены и единицы из файла. Без неё сравнение посчитается, но будет
            помечено «нужны подтверждения».
          </NextStep>
        );
      })()}
      <ImportPanel
        client={client}
        workspace={workspace}
        suppliers={suppliers}
        onImported={refresh}
      />
      <h3>Очередь и результаты</h3>
      {documents.length === 0 ? (
        <p className="muted">Документов пока нет.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Файл</th>
              <th scope="col">Поставщик</th>
              <th scope="col">Статус</th>
              <th scope="col">Этап</th>
              <th scope="col">Строк</th>
              <th scope="col">
                <span className="sr-only">Действия</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => {
              const run = runs[doc.id];
              const canOpen = (run?.counts.lines ?? 0) > 0;
              return (
                <tr key={doc.id}>
                  <td>
                    {doc.original_name}
                    <div className="meta">{doc.extension.toUpperCase()}</div>
                  </td>
                  <td>{supplierName(doc.supplier_id)}</td>
                  <td>
                    <span className={`badge status-${doc.status}`}>
                      {DOCUMENT_STATUS_LABELS[doc.status]}
                    </span>
                  </td>
                  <td aria-live="polite">{stageText(doc, run)}</td>
                  <td>
                    {run ? String(run.counts.lines) : "—"}
                    {review[doc.id] ? (
                      <div className="meta">
                        проверено {String(review[doc.id]?.checked ?? 0)} из{" "}
                        {String(review[doc.id]?.total ?? 0)}
                        {review[doc.id]?.attention
                          ? `, с замечаниями ${String(review[doc.id]?.attention ?? 0)}`
                          : ""}
                      </div>
                    ) : null}
                  </td>
                  <td className="actions">
                    {canOpen ? (
                      <button
                        type="button"
                        className="primary"
                        onClick={() => {
                          setOpenId(doc.id);
                        }}
                      >
                        {review[doc.id] && review[doc.id]?.checked === review[doc.id]?.total
                          ? "Открыть"
                          : "Проверить"}
                      </button>
                    ) : null}
                    {run && ACTIVE_RUN.has(run.status) ? (
                      <button
                        type="button"
                        onClick={() => void act(() => client.cancelRun(run.id))}
                      >
                        Отменить
                      </button>
                    ) : null}
                    {(run?.status === "failed" && run.error?.retryable) ||
                    run?.status === "cancelled" ? (
                      <button type="button" onClick={() => void act(() => client.retryRun(run.id))}>
                        {run.status === "cancelled" ? "Обработать снова" : "Повторить"}
                      </button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
