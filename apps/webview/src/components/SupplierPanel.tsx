import { useCallback, useEffect, useState, type SyntheticEvent } from "react";

import type { ApiClient, Supplier, Workspace } from "../api/client";
import { ErrorNotice } from "./ErrorNotice";
import { NextStep } from "./NextStep";
import type { StepId } from "../journey/progress";

interface Props {
  client: ApiClient;
  workspace: Workspace;
  onGo?: ((step: StepId) => void) | undefined;
}

interface SupplierForm {
  name: string;
  country_code: string;
  default_currency: string;
  tax_id: string;
  terms: string;
}

function emptyForm(ws: Workspace): SupplierForm {
  return {
    name: "",
    country_code: ws.country_code,
    default_currency: ws.base_currency,
    tax_id: "",
    terms: "",
  };
}

function SupplierFields({
  value,
  onChange,
  idPrefix,
}: {
  value: SupplierForm;
  onChange: (next: SupplierForm) => void;
  idPrefix: string;
}) {
  return (
    <>
      <label htmlFor={`${idPrefix}-name`}>
        Название
        <input
          id={`${idPrefix}-name`}
          value={value.name}
          maxLength={200}
          required
          onChange={(e) => {
            onChange({ ...value, name: e.target.value });
          }}
        />
      </label>
      <label htmlFor={`${idPrefix}-country`}>
        Страна
        <input
          id={`${idPrefix}-country`}
          value={value.country_code}
          minLength={2}
          maxLength={2}
          required
          onChange={(e) => {
            onChange({ ...value, country_code: e.target.value.toUpperCase() });
          }}
        />
      </label>
      <label htmlFor={`${idPrefix}-currency`}>
        Валюта по умолчанию
        <input
          id={`${idPrefix}-currency`}
          value={value.default_currency}
          minLength={3}
          maxLength={3}
          required
          onChange={(e) => {
            onChange({ ...value, default_currency: e.target.value.toUpperCase() });
          }}
        />
      </label>
      <label htmlFor={`${idPrefix}-tax`}>
        Налоговый идентификатор (необязательно)
        <input
          id={`${idPrefix}-tax`}
          value={value.tax_id}
          maxLength={64}
          onChange={(e) => {
            onChange({ ...value, tax_id: e.target.value });
          }}
        />
      </label>
      <label htmlFor={`${idPrefix}-terms`} className="wide">
        Условия (необязательно)
        <textarea
          id={`${idPrefix}-terms`}
          value={value.terms}
          maxLength={4000}
          rows={2}
          onChange={(e) => {
            onChange({ ...value, terms: e.target.value });
          }}
        />
      </label>
    </>
  );
}

export function SupplierPanel({ client, workspace, onGo }: Props) {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [form, setForm] = useState<SupplierForm>(() => emptyForm(workspace));
  const [editing, setEditing] = useState<{ supplier: Supplier; form: SupplierForm } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (cursor?: string) => {
      try {
        const params: Parameters<ApiClient["listSuppliers"]>[0] = {
          workspaceId: workspace.id,
          includeArchived,
        };
        if (query.trim()) params.query = query.trim();
        if (cursor) params.cursor = cursor;
        const page = await client.listSuppliers(params);
        setSuppliers((prev) => (cursor ? [...prev, ...page.items] : page.items));
        setNextCursor(page.next_cursor);
      } catch (e: unknown) {
        setError(e);
      }
    },
    [client, workspace.id, query, includeArchived],
  );

  useEffect(() => {
    const timer = setTimeout(() => void load(), 200);
    return () => {
      clearTimeout(timer);
    };
  }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e: unknown) {
      setError(e);
    } finally {
      setBusy(false);
      await load();
    }
  }

  function create(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    void run(async () => {
      await client.createSupplier({
        workspace_id: workspace.id,
        name: form.name,
        country_code: form.country_code,
        default_currency: form.default_currency,
        tax_id: form.tax_id.trim() || null,
        terms: form.terms.trim() || null,
      });
      setForm(emptyForm(workspace));
    });
  }

  function saveEdit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    const { supplier, form: f } = editing;
    void run(async () => {
      await client.updateSupplier(supplier.id, supplier.version, {
        name: f.name,
        country_code: f.country_code,
        default_currency: f.default_currency,
        tax_id: f.tax_id.trim() || null,
        terms: f.terms.trim() || null,
      });
      setEditing(null);
    });
  }

  function toggleArchive(supplier: Supplier) {
    void run(() =>
      client.updateSupplier(supplier.id, supplier.version, {
        archived: supplier.archived_at === null,
      }),
    );
  }

  return (
    <section aria-labelledby="sup-title">
      <header className="page-head">
        <div>
          <span className="eyebrow">{workspace.name}</span>
          <h1 id="sup-title">Поставщики</h1>
          <p className="lede">Кто присылает прайсы. Поставщика можно указать и при загрузке.</p>
        </div>
      </header>
      <NextStep
        title={
          suppliers.length
            ? "загрузите прайс поставщика"
            : "добавьте поставщика или сразу загрузите прайс"
        }
        action={{ label: "Загрузить прайс", onClick: () => onGo?.("import") }}
      >
        {suppliers.length
          ? "Поставщик выбирается при загрузке файла."
          : "Поставщик нужен, чтобы различать предложения в сравнении; его можно указать и при загрузке файла."}
      </NextStep>
      <ErrorNotice
        error={error}
        onDismiss={() => {
          setError(null);
        }}
      />

      <div className="toolbar">
        <label>
          Поиск по названию
          <input
            type="search"
            value={query}
            maxLength={200}
            onChange={(e) => {
              setQuery(e.target.value);
            }}
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(e) => {
              setIncludeArchived(e.target.checked);
            }}
          />
          Показывать архивных
        </label>
      </div>

      {suppliers.length === 0 ? (
        <p className="muted">Поставщики не найдены.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Название</th>
              <th scope="col">Страна</th>
              <th scope="col">Валюта</th>
              <th scope="col">Налоговый ID</th>
              <th scope="col">Статус</th>
              <th scope="col">
                <span className="sr-only">Действия</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {suppliers.map((s) => (
              <tr key={s.id}>
                <td>
                  {s.name}
                  {s.terms ? <div className="meta">{s.terms}</div> : null}
                </td>
                <td>{s.country_code}</td>
                <td>{s.default_currency}</td>
                <td>{s.tax_id ?? "—"}</td>
                <td>{s.archived_at ? <span className="badge">В архиве</span> : "Активен"}</td>
                <td className="actions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setEditing({
                        supplier: s,
                        form: {
                          name: s.name,
                          country_code: s.country_code,
                          default_currency: s.default_currency,
                          tax_id: s.tax_id ?? "",
                          terms: s.terms ?? "",
                        },
                      });
                    }}
                  >
                    Изменить
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      toggleArchive(s);
                    }}
                  >
                    {s.archived_at ? "Восстановить" : "В архив"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {nextCursor ? (
        <button type="button" onClick={() => void load(nextCursor)}>
          Показать ещё
        </button>
      ) : null}

      {editing ? (
        <form className="card form-grid" onSubmit={saveEdit} aria-label="Изменение поставщика">
          <h3>Изменить: {editing.supplier.name}</h3>
          <SupplierFields
            idPrefix="edit"
            value={editing.form}
            onChange={(next) => {
              setEditing({ ...editing, form: next });
            }}
          />
          <div>
            <button type="submit" className="primary" disabled={busy}>
              Сохранить
            </button>{" "}
            <button
              type="button"
              onClick={() => {
                setEditing(null);
              }}
            >
              Отмена
            </button>
          </div>
        </form>
      ) : (
        <form className="card form-grid" onSubmit={create} aria-label="Новый поставщик">
          <h3>Новый поставщик</h3>
          <SupplierFields idPrefix="new" value={form} onChange={setForm} />
          <div>
            <button type="submit" className="primary" disabled={busy}>
              Добавить
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
