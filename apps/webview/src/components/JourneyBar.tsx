import { currentStep, steps, type Progress, type StepId } from "../journey/progress";

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
          const state = step.done ? "done" : step.id === next ? "next" : "later";
          return (
            <li key={step.id} className={`journey-step journey-${state}`}>
              <button
                type="button"
                aria-current={active === step.id ? "step" : undefined}
                aria-label={`Шаг ${String(index + 1)}: ${step.title} — ${step.done ? "готово" : state === "next" ? "следующий шаг" : "позже"}, ${step.hint}`}
                onClick={() => {
                  onGo(step.id);
                }}
              >
                <span className="journey-index" aria-hidden="true">
                  {step.done ? "✓" : String(index + 1)}
                </span>
                <span>
                  {step.title}
                  <span className="journey-hint">
                    {step.hint}
                    {state === "next" ? " · следующий шаг" : ""}
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
