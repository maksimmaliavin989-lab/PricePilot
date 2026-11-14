import type { Health, Workspace } from "../api/client";
import {
  STEP_STATE_LABELS,
  currentStep,
  steps,
  type Progress,
  type StepId,
} from "../journey/progress";

export type View = "workspaces" | "suppliers" | "documents" | "matching" | "comparison";

interface Props {
  workspaces: readonly Workspace[];
  active: Workspace | null;
  view: View;
  progress: Progress;
  health: Health | null;
  failed: boolean;
  onSelectWorkspace: (id: string) => void;
  onView: (view: View) => void;
  onStep: (step: StepId) => void;
}

const STEP_VIEW: Record<StepId, View> = {
  import: "documents",
  review: "documents",
  match: "matching",
  compare: "comparison",
};

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path
        d="M3 7.5l2.5 2.5L11 4.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Navigation: the workspace, the four steps of the path with their state, the rest below. */
export function Sidebar({
  workspaces,
  active,
  view,
  progress,
  health,
  failed,
  onSelectWorkspace,
  onView,
  onStep,
}: Props) {
  const next = currentStep(progress);
  return (
    <nav className="sidebar" aria-label="Основная навигация">
      <p className="brand">
        <svg className="brand-mark" width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
          <rect width="28" height="28" rx="8" fill="var(--accent)" />
          <path
            d="M8 18l5-8 4 5 3-3"
            fill="none"
            stroke="var(--accent-text)"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        PricePilot
      </p>

      {workspaces.length ? (
        <label className="workspace-switch">
          Рабочее пространство
          <select
            value={active?.id ?? ""}
            onChange={(e) => {
              if (e.target.value) onSelectWorkspace(e.target.value);
            }}
          >
            {active ? null : <option value="">— выберите —</option>}
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <div>
        <span className="side-label">Путь закупки</span>
        <ol className="side-group side-group-steps" aria-label="Путь: от прайса до сравнения">
          {steps(progress).map((step, index) => {
            const isNext = active !== null && step.id === next && step.state !== "done";
            // Upload and review share the documents screen; the upload step marks it.
            const current = step.id !== "review" && STEP_VIEW[step.id] === view;
            return (
              <li key={step.id}>
                <button
                  type="button"
                  className={`step-link step-${step.state}${isNext ? " step-next" : ""}`}
                  aria-current={current ? "page" : undefined}
                  aria-label={`Шаг ${String(index + 1)}: ${step.title} — ${STEP_STATE_LABELS[step.state]}${isNext ? ", следующий шаг" : ""}, ${step.hint}`}
                  disabled={!active}
                  onClick={() => {
                    onStep(step.id);
                  }}
                >
                  <span className="step-index" aria-hidden="true">
                    {step.state === "done" ? <CheckIcon /> : String(index + 1)}
                  </span>
                  <span className="step-text">
                    <span className="step-title">{step.title}</span>
                    <span className="step-hint">
                      {STEP_STATE_LABELS[step.state]} · {step.hint}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </div>

      <ul className="side-group">
        <li>
          <button
            type="button"
            className="side-link"
            aria-current={view === "suppliers" ? "page" : undefined}
            disabled={!active}
            onClick={() => {
              onView("suppliers");
            }}
          >
            Поставщики
          </button>
        </li>
        <li>
          <button
            type="button"
            className="side-link"
            aria-current={view === "workspaces" ? "page" : undefined}
            onClick={() => {
              onView("workspaces");
            }}
          >
            Рабочие пространства и копии
          </button>
        </li>
      </ul>

      <p className="side-status" role="status">
        <span className={failed ? "dot dot-bad" : "dot"} aria-hidden="true" />
        {health
          ? `Работает локально · база ${health.database === "ok" ? "доступна" : health.database}`
          : failed
            ? "Локальный сервис недоступен"
            : "Запуск локального сервиса…"}
      </p>
    </nav>
  );
}
