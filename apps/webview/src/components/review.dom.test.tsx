// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, type Field, type Line, type SourceDocument } from "../api/client";
import { makeField, makeLine } from "../review/testData";
import { DocumentReview } from "./DocumentReview";
import { FieldEditor } from "./FieldEditor";
import { ImportPanel } from "./ImportPanel";

afterEach(() => {
  cleanup();
});

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

const DOC = {
  id: "d1",
  original_name: "Прайс_1.xlsx",
  status: "review_required",
} as unknown as SourceDocument;

function reviewClient(lines: Line[], onPatch?: (body: unknown) => Field) {
  return new ApiClient({ baseUrl: "", token: null }, (input, init) => {
    const url = new URL(input instanceof Request ? input.url : input.toString(), "http://x");
    if (url.pathname.endsWith("/lines")) return json({ items: lines, next_cursor: null });
    if (url.pathname.endsWith("/units"))
      return json([
        { code: "pc", dimension: "count", ucum: "{piece}", label_ru: "шт", label_en: "pc" },
      ]);
    if (url.pathname.endsWith("/source-rows")) {
      return json({
        sheet: "Прайс-лист",
        sheets: ["Прайс-лист"],
        first_row: 2,
        last_row: 12,
        total_rows: 53,
        rows: [{ index: 7, cells: [{ column: 2, ref: "B7", text: "Цемент", formula: null }] }],
      });
    }
    if (init?.method === "PATCH" && onPatch) {
      return json(onPatch(JSON.parse(init.body as string)));
    }
    return json({}, 404);
  });
}

describe("DocumentReview", () => {
  const lines = [
    makeLine("l1", 1, "Цемент"),
    makeLine("l2", 2, "Арматура", { reasons: ["UNKNOWN_UNIT"] }),
    makeLine("l3", 3, "Брус"),
  ];

  it("keeps the selected line and its source while sorting and filtering", async () => {
    render(
      <DocumentReview
        client={reviewClient(lines)}
        document={DOC}
        supplierName="ООО"
        onBack={vi.fn()}
      />,
    );
    const table = await screen.findByRole("table", { name: "Извлечённые строки" });
    fireEvent.click(within(table).getByText("Брус"));
    expect(screen.getByRole("heading", { name: "Поля строки 9" })).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Сортировка"), { target: { value: "name" } });
    fireEvent.click(screen.getByRole("button", { name: "С замечаниями · 1" }));
    expect(within(table).queryByText("Брус")).toBeNull();
    // Still selected, and the panel says so in text.
    expect(screen.getByRole("heading", { name: "Поля строки 9" })).toBeTruthy();
    expect(screen.getByText("Строка скрыта текущим фильтром, но остаётся выбранной.")).toBeTruthy();
    expect(screen.getByText("Есть замечания", { selector: ".badge" })).toBeTruthy();
  });

  it("counts unchecked lines separately and offers the next step", async () => {
    render(
      <DocumentReview
        client={reviewClient(lines)}
        document={DOC}
        supplierName="ООО"
        onBack={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    await screen.findByRole("table", { name: "Извлечённые строки" });
    const filter = screen.getByRole("group", { name: "Показать" });
    expect(within(filter).getByText("Не проверено · 2")).toBeTruthy();
    expect(within(filter).getByText("С замечаниями · 1")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Проверено 0 из 3");
    const next = screen.getByRole("region", { name: "Следующий шаг" });
    expect(next.textContent).toContain("разберите строки с замечаниями (1)");
    expect(
      within(next).getByRole("button", { name: "Подтвердить строки без замечаний (2)" }),
    ).toBeTruthy();
  });

  it("moves the selection with the arrow keys", async () => {
    render(
      <DocumentReview
        client={reviewClient(lines)}
        document={DOC}
        supplierName="ООО"
        onBack={vi.fn()}
      />,
    );
    const table = await screen.findByRole("table", { name: "Извлечённые строки" });
    const rows = within(table).getAllByRole("row").slice(1);
    const first = rows[0];
    if (!first) throw new Error("no rows");
    fireEvent.keyDown(first, { key: "ArrowDown" });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Поля строки 8" })).toBeTruthy();
    });
    expect(rows[1]?.getAttribute("aria-selected")).toBe("true");
  });

  it("shows the source cell of the selected field", async () => {
    render(
      <DocumentReview
        client={reviewClient(lines)}
        document={DOC}
        supplierName="ООО"
        onBack={vi.fn()}
      />,
    );
    const source = await screen.findByRole("table", { name: "Фрагмент исходного файла" });
    expect(within(source).getByText("(выбранная строка)", { exact: false })).toBeTruthy();
    expect(screen.getByText(/строка 7/)).toBeTruthy();
  });
});

describe("FieldEditor", () => {
  it("sends a canonical value with the expected revision and keeps the raw value", async () => {
    const field = makeField({
      field_key: "price",
      value_type: "money",
      raw_value: "1 234,5 руб.",
      normalized_value: null,
      revision: 3,
    });
    let sent: unknown = null;
    const client = reviewClient([], (body) => {
      sent = body;
      return {
        ...field,
        normalized_value: { amount: "1234.50", currency: "RUB" },
        review_status: "corrected",
        revision: 4,
      };
    });
    const onChanged = vi.fn();
    render(
      <ul>
        <FieldEditor
          client={client}
          field={field}
          units={[]}
          selected
          onSelect={vi.fn()}
          onChanged={onChanged}
        />
      </ul>,
    );
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Подтвердить" }).disabled).toBe(
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: "Исправить" }));
    fireEvent.change(screen.getByLabelText("Сумма"), { target: { value: "1,234" } });
    fireEvent.change(screen.getByLabelText("Валюта"), { target: { value: "rub" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить исправление" }));
    expect(screen.getByRole("alert").textContent).toContain("неоднозначных");
    expect(sent).toBeNull();

    fireEvent.change(screen.getByLabelText("Сумма"), { target: { value: "1 234,50" } });
    fireEvent.change(screen.getByLabelText("Причина (сохраняется в истории)"), {
      target: { value: "Разделитель тысяч" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить исправление" }));
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalled();
    });
    expect(sent).toEqual({
      value: { amount: "1234.50", currency: "RUB" },
      reason: "Разделитель тысяч",
      expected_revision: 3,
    });
    expect(screen.getByText("1 234,5 руб.")).toBeTruthy();
  });
});

describe("ImportPanel", () => {
  it("uploads dropped files and reports each result in text", async () => {
    const client = new ApiClient({ baseUrl: "", token: null }, () =>
      json(
        {
          document: { id: "d" },
          run: null,
          duplicate_of: null,
          error: { code: "UNSUPPORTED_FORMAT", message: "PDF" },
        },
        201,
      ),
    );
    const onImported = vi.fn();
    render(
      <ImportPanel
        client={client}
        workspace={{ id: "w1" } as never}
        suppliers={[]}
        onImported={onImported}
      />,
    );
    const zone = screen.getByRole("button", { name: /Перетащите файлы/ });
    fireEvent.drop(zone, { dataTransfer: { files: [new File(["%PDF"], "прайс.pdf")] } });
    expect(
      await screen.findByText("Сохранён, не обработан: Формат не поддерживается"),
    ).toBeTruthy();
    expect(onImported).toHaveBeenCalled();
  });
});
