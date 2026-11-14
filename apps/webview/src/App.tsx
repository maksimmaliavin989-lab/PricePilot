import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClient, type Health, type Workspace } from "./api/client";
import { isTauri, resolveConnection } from "./api/connection";
import { ErrorNotice } from "./components/ErrorNotice";
import { BackupPanel } from "./components/BackupPanel";
import { ComparisonView } from "./components/ComparisonView";
import { DocumentsView } from "./components/DocumentsView";
import { MatchingView } from "./components/MatchingView";
import { Sidebar, type View } from "./components/Sidebar";
import { SupplierPanel } from "./components/SupplierPanel";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { EMPTY_PROGRESS, loadProgress, type Progress, type StepId } from "./journey/progress";

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
  const [view, setView] = useState<View>(() => (readActive() ? "documents" : "workspaces"));
  const [focusDocument, setFocusDocument] = useState<string | null>(null);
  const [reviewRequest, setReviewRequest] = useState(0);
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

  function select(id: string) {
    setActiveId(id);
    writeActive(id);
    setView("documents");
  }

  return (
    <div className="shell">
      <Sidebar
        workspaces={workspaces}
        active={active}
        view={view}
        progress={progress}
        health={health}
        failed={Boolean(fatal)}
        onSelectWorkspace={(id) => {
          setActiveId(id);
          writeActive(id);
          setView("documents");
        }}
        onView={setView}
        onStep={(step) => {
          if (step === "review") setReviewRequest((n) => n + 1);
          go(step);
        }}
      />
      <main className="content">
        <ErrorNotice error={fatal} />
        {health?.migration ? (
          <div className="notice" role="status">
            База данных обновлена до новой версии схемы ({health.migration.to_revision}). Перед
            обновлением создана резервная копия: {health.migration.backup}.
          </div>
        ) : null}
        {client && (view === "workspaces" || !active) ? (
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
            key={`${active.id}-${String(reviewRequest)}`}
            client={client}
            workspace={active}
            openReview={reviewRequest > 0}
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
