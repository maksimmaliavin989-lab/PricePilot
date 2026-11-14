import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClient, type Health, type Workspace } from "./api/client";
import { isTauri, resolveConnection } from "./api/connection";
import { ErrorNotice } from "./components/ErrorNotice";
import { JourneyBar } from "./components/JourneyBar";
import { BackupPanel } from "./components/BackupPanel";
import { ComparisonView } from "./components/ComparisonView";
import { DocumentsView } from "./components/DocumentsView";
import { MatchingView } from "./components/MatchingView";
import { SupplierPanel } from "./components/SupplierPanel";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { EMPTY_PROGRESS, loadProgress, type Progress, type StepId } from "./journey/progress";

type View = "workspaces" | "suppliers" | "documents" | "matching" | "comparison";
const ACTIVE_KEY = "pricepilot.activeWorkspace";

function readActive(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY);
  } catch {
    return null;
  }
}

function writeActive(id: string): void {
  try {
    localStorage.setItem(ACTIVE_KEY, id);
  } catch {
    /* preference only */
  }
}

async function fetchAllWorkspaces(client: ApiClient): Promise<Workspace[]> {
  const items: Workspace[] = [];
  let cursor: string | undefined;
  do {
    const page = await client.listWorkspaces(cursor);
    items.push(...page.items);
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
  return items;
}

export function App() {
  const [client, setClient] = useState<ApiClient | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [fatal, setFatal] = useState<unknown>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeId, setActiveId] = useState<string | null>(readActive);
  const [view, setView] = useState<View>("workspaces");
  const [focusDocument, setFocusDocument] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress>(EMPTY_PROGRESS);
  const [progressTick, setProgressTick] = useState(0);
  const refreshProgress = useCallback(() => {
    setProgressTick((t) => t + 1);
  }, []);

  useEffect(() => {
    const state = { cancelled: false };
    void (async () => {
      try {
        const api = new ApiClient(
          await resolveConnection(),
          undefined,
          isTauri() ? resolveConnection : undefined,
        );
        const status = await api.health();
        const items = await fetchAllWorkspaces(api);
        if (state.cancelled) return;
        setClient(api);
        setHealth(status);
        setWorkspaces(items);
      } catch (e: unknown) {
        if (!state.cancelled) setFatal(e);
      }
    })();
    return () => {
      state.cancelled = true;
    };
  }, []);

  const reloadWorkspaces = useCallback(async () => {
    if (!client) return;
    setWorkspaces(await fetchAllWorkspaces(client));
  }, [client]);

  const active = useMemo(
    () => workspaces.find((w) => w.id === activeId) ?? null,
    [workspaces, activeId],
  );

  useEffect(() => {
    if (!client || !active) return undefined;
    const state = { cancelled: false };
    loadProgress(client, active.id).then(
      (p) => {
        if (!state.cancelled) setProgress(p);
      },
      () => undefined, // the path bar is a hint; screens show their own errors
    );
    return () => {
      state.cancelled = true;
    };
  }, [client, active, view, progressTick]);

  const go = useCallback((step: StepId, documentId?: string) => {
    setFocusDocument(documentId ?? null);
    setView(step === "match" ? "matching" : step === "compare" ? "comparison" : "documents");
  }, []);

  const activeStep: StepId | null =
    view === "documents"
      ? "review"
      : view === "matching"
        ? "match"
        : view === "comparison"
          ? "compare"
          : null;

  function select(id: string) {
    setActiveId(id);
    writeActive(id);
    setView("suppliers");
  }

  return (
    <div className="app">
      <header className="header">
        <h1>PricePilot</h1>
        <nav aria-label="Основная навигация">
          <button
            type="button"
            aria-current={view === "workspaces" ? "page" : undefined}
            onClick={() => {
              setView("workspaces");
            }}
          >
            Рабочие пространства
          </button>
          <button
            type="button"
            aria-current={view === "suppliers" ? "page" : undefined}
            disabled={!active}
            onClick={() => {
              setView("suppliers");
            }}
          >
            Поставщики
          </button>
          <button
            type="button"
            aria-current={view === "documents" ? "page" : undefined}
            disabled={!active}
            onClick={() => {
              setView("documents");
            }}
          >
            Документы
          </button>
          <button
            type="button"
            aria-current={view === "matching" ? "page" : undefined}
            disabled={!active}
            onClick={() => {
              setView("matching");
            }}
          >
            Сопоставление
          </button>
          <button
            type="button"
            aria-current={view === "comparison" ? "page" : undefined}
            disabled={!active}
            onClick={() => {
              setView("comparison");
            }}
          >
            Сравнение
          </button>
        </nav>
        <p className="status" role="status">
          {health
            ? `Локальный режим · API ${health.api_version} · база данных: ${health.database === "ok" ? "доступна" : health.database}`
            : fatal
              ? "Локальный сервис недоступен"
              : "Запуск локального сервиса…"}
        </p>
      </header>
      {active && view !== "workspaces" ? (
        <JourneyBar progress={progress} active={activeStep} onGo={go} />
      ) : null}
      <main>
        <ErrorNotice error={fatal} />
        {health?.migration ? (
          <div className="notice" role="status">
            База данных обновлена до новой версии схемы ({health.migration.to_revision}). Перед
            обновлением создана резервная копия: {health.migration.backup}.
          </div>
        ) : null}
        {client && view === "workspaces" ? (
          <>
            <WorkspacePanel
              client={client}
              workspaces={workspaces}
              activeId={active?.id ?? null}
              onSelect={select}
              onChanged={reloadWorkspaces}
            />
            <BackupPanel client={client} />
          </>
        ) : null}
        {client && view === "suppliers" && active ? (
          <SupplierPanel key={active.id} client={client} workspace={active} onGo={go} />
        ) : null}
        {client && view === "documents" && active ? (
          <DocumentsView
            key={active.id}
            client={client}
            workspace={active}
            onGo={go}
            onProgress={refreshProgress}
          />
        ) : null}
        {client && view === "matching" && active ? (
          <MatchingView
            key={`${active.id}-${focusDocument ?? ""}`}
            client={client}
            workspace={active}
            initialDocumentId={focusDocument}
            onGo={go}
            onProgress={refreshProgress}
          />
        ) : null}
        {client && view === "comparison" && active ? (
          <ComparisonView
            key={active.id}
            client={client}
            workspace={active}
            onGo={go}
            onProgress={refreshProgress}
          />
        ) : null}
      </main>
    </div>
  );
}
