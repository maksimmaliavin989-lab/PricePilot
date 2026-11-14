// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Workspace } from "../api/client";
import { EMPTY_PROGRESS } from "../journey/progress";
import { Sidebar } from "./Sidebar";

afterEach(() => {
  cleanup();
});

const WS = { id: "w1", name: "Стройка Север" } as Workspace;

describe("Sidebar", () => {
  it("shows every step with its state in words and leads to it", () => {
    const onStep = vi.fn();
    render(
      <Sidebar
        workspaces={[WS]}
        active={WS}
        view="matching"
        progress={{
          ...EMPTY_PROGRESS,
          documents: 1,
          lines: 41,
          checkedLines: 41,
          offerLines: 41,
          matchedLines: 1,
        }}
        health={null}
        failed={false}
        onSelectWorkspace={vi.fn()}
        onView={vi.fn()}
        onStep={onStep}
      />,
    );
    const match = screen.getByRole("button", { name: /Шаг 3: Ваши товары — в процессе/ });
    expect(match.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("button", { name: /Шаг 1: Прайсы — готово/ })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Шаг 4: Сравнение — не начато, следующий шаг/ }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Шаг 2: Проверка/ }));
    expect(onStep).toHaveBeenCalledWith("review");
  });

  it("disables the path until a workspace is chosen", () => {
    render(
      <Sidebar
        workspaces={[WS]}
        active={null}
        view="workspaces"
        progress={EMPTY_PROGRESS}
        health={null}
        failed={false}
        onSelectWorkspace={vi.fn()}
        onView={vi.fn()}
        onStep={vi.fn()}
      />,
    );
    const first = screen.getByRole<HTMLButtonElement>("button", { name: /Шаг 1: Прайсы/ });
    expect(first.disabled).toBe(true);
  });
});
