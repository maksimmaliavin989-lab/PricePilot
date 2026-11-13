import { ApiError, type ApiClient, type ImportResult } from "../api/client";

export type UploadState = "waiting" | "uploading" | "done" | "rejected";

export interface UploadItem {
  key: string;
  name: string;
  size: number;
  state: UploadState;
  result?: ImportResult;
  error?: { code: string; message: string; correlationId: string };
}

export const ACCEPT =
  ".csv,.txt,.xlsx,.xls,.docx,.pdf,.jpg,.jpeg,.png,.tif,.tiff,.heic,text/csv," +
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

let counter = 0;

export function toItems(files: File[]): UploadItem[] {
  return files.map((file) => {
    counter += 1;
    return {
      key: `${String(counter)}:${file.name}`,
      name: file.name,
      size: file.size,
      state: "waiting",
    };
  });
}

/**
 * Uploads files one after another. A rejected file (empty, too large, network error) is
 * reported on its own item and never stops the rest of the batch.
 */
export async function uploadAll(
  client: ApiClient,
  files: File[],
  items: UploadItem[],
  params: { workspaceId: string; supplierId?: string; source: "file_picker" | "drag_drop" },
  onChange: (item: UploadItem) => void,
): Promise<void> {
  for (const [index, file] of files.entries()) {
    const item = items[index];
    if (!item) continue;
    onChange({ ...item, state: "uploading" });
    try {
      const result = await client.uploadDocument(file, params);
      onChange({ ...item, state: "done", result });
    } catch (error: unknown) {
      const e =
        error instanceof ApiError
          ? { code: error.code, message: error.message, correlationId: error.correlationId }
          : { code: "UPLOAD_FAILED", message: "Не удалось отправить файл.", correlationId: "" };
      onChange({ ...item, state: "rejected", error: e });
    }
  }
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${String(bytes)} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1).replace(".", ",")} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`;
}
