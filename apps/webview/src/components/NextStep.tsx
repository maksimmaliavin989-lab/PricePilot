import type { ReactNode } from "react";

interface Action {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

interface Props {
  title: string;
  children?: ReactNode;
  action?: Action | undefined;
  secondary?: Action | undefined;
}

/** One clear next step per screen: what to do now and the button that does it. */
export function NextStep({ title, children, action, secondary }: Props) {
  return (
    <div className="next-step" role="region" aria-label="Следующий шаг">
      <div>
        <strong>Следующий шаг: {title}</strong>
        {children ? <div className="meta">{children}</div> : null}
      </div>
      <div className="actions">
        {action ? (
          <button
            type="button"
            className="primary"
            disabled={action.disabled}
            onClick={action.onClick}
          >
            {action.label}
          </button>
        ) : null}
        {secondary ? (
          <button type="button" disabled={secondary.disabled} onClick={secondary.onClick}>
            {secondary.label}
          </button>
        ) : null}
      </div>
    </div>
  );
}
