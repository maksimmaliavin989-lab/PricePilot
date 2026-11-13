import { useEffect, useState } from "react";

import type { ApiClient, Backup } from "../api/client";
import { formatSize } from "../review/upload";
import { ErrorNotice } from "./ErrorNotice";

/** Database backups. Restoring needs the application closed, so it is a documented
 * command-line step rather than a button. */
export function BackupPanel({ client }: { client: ApiClient }) {
  const [backups, setBackups] = useState<Backup[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    client.listBackups().then(
      (list) => {
        if (!cancelled) setBackups(list);
      },
      (e: unknown) => {
        if (!cancelled) setError(e);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [client]);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const created = await client.createBackup();
      setBackups((prev) => [created, ...(prev ?? [])]);
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="backup-title" className="card">
      <h3 id="backup-title">Резервные копии базы</h3>
      <p className="meta">
        Копии лежат в папке данных приложения, в подпапке backups. Перед обновлением схемы базы
        копия создаётся автоматически. Оригиналы файлов хранятся отдельно и не изменяются.
      </p>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      <button type="button" disabled={busy} onClick={() => void create()}>
        Создать копию
      </button>
      {backups && backups.length > 0 ? (
        <ul className="list upload-list">
          {backups.slice(0, 10).map((b) => (
            <li key={b.name} className="list-item">
              <span>{b.name}</span>
              <span className="meta">
                {formatSize(b.size_bytes)} · {new Date(b.created_at).toLocaleString("ru-RU")}
              </span>
            </li>
          ))}
        </ul>
      ) : backups ? (
        <p className="muted">Копий пока нет.</p>
      ) : null}
    </section>
  );
}
