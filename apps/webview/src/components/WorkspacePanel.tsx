import { useState, type SyntheticEvent } from "react";

import type { ApiClient, Workspace } from "../api/client";
import { ErrorNotice } from "./ErrorNotice";

interface Props {
  client: ApiClient;
  workspaces: Workspace[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onChanged: () => Promise<void>;
}

const EMPTY = { name: "", locale: "ru-RU", base_currency: "RUB", country_code: "RU" };

export function WorkspacePanel({ client, workspaces, activeId, onSelect, onChanged }: Props) {
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState<{ id: string; name: string; version: number } | null>(
    null,
  );

  async function create(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await client.createWorkspace(form);
      setForm(EMPTY);
      await onChanged();
      onSelect(created.id);
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function rename(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      await client.updateWorkspace(editing.id, editing.version, { name: editing.name });
      setEditing(null);
      await onChanged();
    } catch (e: unknown) {
      setError(e);
      await onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="ws-title">
      <header className="page-head">
        <div>
          <h1 id="ws-title">Рабочие пространства</h1>
          <p className="lede">
            Отдельные проекты закупок: у каждого свои прайсы, товары и сравнения. Данные хранятся на
            этом компьютере.
          </p>
        </div>
      </header>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />
      {workspaces.length === 0 ? (
        <p className="muted">
          Пока нет рабочих пространств. Создайте первое — данные хранятся только на этом компьютере.
        </p>
      ) : (
        <ul className="list">
          {workspaces.map((ws) => (
            <li key={ws.id} className={ws.id === activeId ? "list-item active" : "list-item"}>
              {editing?.id === ws.id ? (
                <form className="inline" onSubmit={(e) => void rename(e)}>
                  <label className="sr-only" htmlFor={`ws-name-${ws.id}`}>
                    Новое название
                  </label>
                  <input
                    id={`ws-name-${ws.id}`}
                    value={editing.name}
                    maxLength={200}
                    required
                    onChange={(e) => {
                      setEditing({ ...editing, name: e.target.value });
                    }}
                  />
                  <button type="submit" disabled={busy}>
                    Сохранить
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(null);
                    }}
                  >
                    Отмена
                  </button>
                </form>
              ) : (
                <>
                  <div>
                    <strong>{ws.name}</strong>
                    <span className="meta">
                      {" "}
                      {ws.locale} · {ws.base_currency} · {ws.country_code} · хранение: локально
                    </span>
                    {ws.id === activeId ? <span className="badge">Текущее</span> : null}
                  </div>
                  <div className="actions">
                    {ws.id !== activeId ? (
                      <button
                        type="button"
                        onClick={() => {
                          onSelect(ws.id);
                        }}
                      >
                        Открыть
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => {
                        setEditing({ id: ws.id, name: ws.name, version: ws.version });
                      }}
                    >
                      Переименовать
                    </button>
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <form className="card form-grid" onSubmit={(e) => void create(e)}>
        <h3>Новое рабочее пространство</h3>
        <label>
          Название
          <input
            value={form.name}
            maxLength={200}
            required
            onChange={(e) => {
              setForm({ ...form, name: e.target.value });
            }}
          />
        </label>
        <label>
          Локаль (BCP 47)
          <input
            value={form.locale}
            required
            onChange={(e) => {
              setForm({ ...form, locale: e.target.value });
            }}
          />
        </label>
        <label>
          Базовая валюта (ISO 4217)
          <input
            value={form.base_currency}
            minLength={3}
            maxLength={3}
            required
            onChange={(e) => {
              setForm({ ...form, base_currency: e.target.value.toUpperCase() });
            }}
          />
        </label>
        <label>
          Страна (ISO 3166-1)
          <input
            value={form.country_code}
            minLength={2}
            maxLength={2}
            required
            onChange={(e) => {
              setForm({ ...form, country_code: e.target.value.toUpperCase() });
            }}
          />
        </label>
        <div>
          <button type="submit" className="primary" disabled={busy}>
            Создать
          </button>
        </div>
      </form>
    </section>
  );
}
