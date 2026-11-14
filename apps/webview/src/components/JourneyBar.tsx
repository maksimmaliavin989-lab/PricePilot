import {
  STEP_STATE_LABELS,
  currentStep,
  steps,
  type Progress,
  type StepId,
} from "../journey/progress";

interface Props {
  progress: Progress;
  active: StepId | null;
  onGo: (step: StepId) => void;
}

/** The main path, always visible: where the user is, what is done, what comes next. */
export function JourneyBar({ progress, active, onGo }: Props) {
  const next = currentStep(progress);
  return (
    <nav className="journey" aria-label="Путь: от прайса до сравнения">
      <ol>
        {steps(progress).map((step, index) => {
          const isNext = step.id === next && step.state !== "done";
          return (
            <li
              key={step.id}
              className={`journey-step journey-${step.state}${isNext ? " journey-next" : ""}`}
            >
              <button
                type="button"
                aria-current={active === step.id ? "step" : undefined}
                aria-label={`Шаг ${String(index + 1)}: ${step.title} — ${STEP_STATE_LABELS[step.state]}${isNext ? ", следующий шаг" : ""}, ${step.hint}`}
                onClick={() => {
                  onGo(step.id);
                }}
              >
                <span className="journey-index" aria-hidden="true">
                  {step.state === "done" ? "✓" : String(index + 1)}
                </span>
                <span>
                  {step.title}
                  <span className="journey-hint">
                    <span className="journey-state">{STEP_STATE_LABELS[step.state]}</span> ·{" "}
                    {step.hint}
                    {isNext ? " · следующий шаг" : ""}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
