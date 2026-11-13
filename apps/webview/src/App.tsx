import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClient, type Health, type Workspace } from "./api/client";
import { isTauri, resolveConnection } from "./api/connection";
import { ErrorNotice } from "./components/ErrorNotice";
import { BackupPanel } from "./components/BackupPanel";
import { DocumentsView } from "./components/DocumentsView";
import { SupplierPanel } from "./components/SupplierPanel";
import { WorkspacePanel } from "./components/WorkspacePanel";

type View = "workspaces" | "suppliers" | "documents";
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
        </nav>
        <p className="status" role="status">
          {health
            ? `Локальный режим · API ${health.api_version} · база данных: ${health.database === "ok" ? "доступна" : health.database}`
            : fatal
              ? "Локальный сервис недоступен"
              : "Запуск локального сервиса…"}
        </p>
      </header>
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
          <SupplierPanel key={active.id} client={client} workspace={active} />
        ) : null}
        {client && view === "documents" && active ? (
          <DocumentsView key={active.id} client={client} workspace={active} />
        ) : null}
      </main>
    </div>
  );
}
