// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, type Line } from "../api/client";
import { makeLine } from "../review/testData";
import { LineIssues } from "./LineIssues";

afterEach(() => {
  cleanup();
});

const UNITS = [
  { code: "bag", dimension: "count", ucum: "{bag}", label_ru: "меш", label_en: "bag" },
];

function lineWith(issues: Line["issues"]): Line {
  return {
    ...makeLine("l1", 9, "Клей плиточный"),
    issues,
    open_issues: issues.filter((i) => !i.resolved).length,
  };
}

describe("LineIssues", () => {
  it("offers to add a missing unit and shows the remark as resolved afterwards", async () => {
    const missing = {
      code: "MISSING_UNIT",
      field_key: "unit",
      field_ids: [],
      action: "add_value" as const,
      hint: "В строке нет единицы измерения: выберите единицу.",
      resolved: false,
    };
    const posted: unknown[] = [];
    const after = lineWith([{ ...missing, field_ids: ["u1"], resolved: true }]);
    const client = new ApiClient({ baseUrl: "", token: null }, (_input, init) => {
      posted.push(JSON.parse(init?.body as string));
      return Promise.resolve(new Response(JSON.stringify(after), { status: 201 }));
    });
    const changed = vi.fn();
    const { rerender } = render(
      <LineIssues
        client={client}
        line={lineWith([missing])}
        units={UNITS}
        onSelectField={vi.fn()}
        onLineChanged={changed}
      />,
    );
    expect(screen.getByText(/выберите единицу/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Единица"), { target: { value: "bag" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить единицу" }));
    await waitFor(() => {
      expect(changed).toHaveBeenCalledWith(after);
    });
    expect(posted[0]).toMatchObject({ field_key: "unit", value: { value: "bag" } });
    rerender(
      <LineIssues
        client={client}
        line={after}
        units={UNITS}
        onSelectField={vi.fn()}
        onLineChanged={changed}
      />,
    );
    expect(screen.getByText("Все замечания по строке сняты.")).toBeTruthy();
    expect(screen.getByText("Снятые замечания (1)")).toBeTruthy();
  });

  it("leads to the field that resolves the remark", () => {
    const select = vi.fn();
    render(
      <LineIssues
        client={new ApiClient({ baseUrl: "", token: null })}
        line={lineWith([
          {
            code: "UNKNOWN_UNIT",
            field_key: "unit",
            field_ids: ["f-unit"],
            action: "fix_value",
            hint: "Единица в файле не распознана: исправьте поле «Единица».",
            resolved: false,
          },
        ])}
        units={UNITS}
        onSelectField={select}
        onLineChanged={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Исправить значение: поле «Единица»" }));
    expect(select).toHaveBeenCalledWith("f-unit");
  });
});
