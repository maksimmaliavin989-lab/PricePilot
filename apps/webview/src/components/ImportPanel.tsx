import { useRef, useState, type DragEvent, type KeyboardEvent } from "react";

import type { ApiClient, Supplier, Workspace } from "../api/client";
import { errorLabel } from "../review/format";
import { ACCEPT, formatSize, toItems, uploadAll, type UploadItem } from "../review/upload";

interface Props {
  client: ApiClient;
  workspace: Workspace;
  suppliers: Supplier[];
  onImported: () => void;
}

function itemStatus(item: UploadItem): string {
  if (item.state === "waiting") return "Ожидает отправки";
  if (item.state === "uploading") return "Отправляется…";
  if (item.state === "rejected") return `Не принят: ${item.error?.message ?? ""}`;
  const result = item.result;
  if (!result) return "";
  if (result.duplicate_of) return "Уже импортирован ранее — использована существующая запись";
  if (result.error) return `Сохранён, не обработан: ${errorLabel(result.error.code)}`;
  return "Принят, обработка запущена";
}

export function ImportPanel({ client, workspace, suppliers, onImported }: Props) {
  const [supplierId, setSupplierId] = useState("");
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function start(files: File[], source: "file_picker" | "drag_drop") {
    if (files.length === 0) return;
    const batch = toItems(files);
    setItems((prev) => [...batch, ...prev].slice(0, 50));
    setBusy(true);
    const params: { workspaceId: string; supplierId?: string; source: typeof source } = {
      workspaceId: workspace.id,
      source,
    };
    if (supplierId) params.supplierId = supplierId;
    await uploadAll(client, files, batch, params, (updated) => {
      setItems((prev) => prev.map((i) => (i.key === updated.key ? updated : i)));
      if (updated.state === "done") onImported();
    });
    setBusy(false);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(false);
    void start(Array.from(event.dataTransfer.files), "drag_drop");
  }

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.current?.click();
    }
  }

  return (
    <section aria-labelledby="import-title" className="card">
      <h3 id="import-title">Импорт прайсов</h3>
      <label>
        Поставщик (необязательно)
        <select
          value={supplierId}
          onChange={(e) => {
            setSupplierId(e.target.value);
          }}
        >
          <option value="">Не указан — валюта по умолчанию из рабочего пространства</option>
          {suppliers
            .filter((s) => !s.archived_at)
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
        </select>
      </label>
      <div
        role="button"
        tabIndex={0}
        aria-label="Перетащите файлы сюда или нажмите Enter, чтобы выбрать"
        className={dragOver ? "dropzone dropzone-active" : "dropzone"}
        onClick={() => input.current?.click()}
        onKeyDown={onKey}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => {
          setDragOver(false);
        }}
        onDrop={onDrop}
      >
        <strong>{dragOver ? "Отпустите, чтобы импортировать" : "Перетащите файлы сюда"}</strong>
        <span className="meta">
          или нажмите, чтобы выбрать. XLSX, XLS, CSV, DOCX, PDF (и сканы), JPG, PNG, TIFF. Файлы
          остаются на этом компьютере, оригинал не изменяется.
        </span>
      </div>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        className="sr-only"
        data-testid="file-input"
        tabIndex={-1}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          void start(files, "file_picker");
        }}
      />
      {items.length > 0 ? (
        <ul className="list upload-list" aria-live="polite" aria-busy={busy}>
          {items.map((item) => (
            <li key={item.key} className="list-item">
              <div>
                <strong>{item.name}</strong> <span className="meta">{formatSize(item.size)}</span>
                <div className={item.state === "rejected" ? "status-text bad" : "status-text"}>
                  {itemStatus(item)}
                  {item.error?.correlationId ? (
                    <span className="meta"> ID: {item.error.correlationId}</span>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
