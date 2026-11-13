import type { ApiConnection } from "./connection";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type Workspace = Schemas["WorkspaceOut"];
export type WorkspaceCreate = Schemas["WorkspaceCreate"];
export type WorkspacePatch = Schemas["WorkspacePatchIn"];
export type Supplier = Schemas["SupplierOut"];
export type SupplierCreate = Schemas["SupplierCreate"];
export type SupplierPatch = Schemas["SupplierPatchIn"];
export type Health = Schemas["HealthOut"];
type ErrorEnvelope = Schemas["ErrorResponse"];

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

/** Error from the local API with a stable `code` and the `correlationId` for diagnostics. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string;
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    correlationId: string,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
    this.details = details;
  }
}

function isEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error: unknown = value.error;
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string" &&
    "message" in error &&
    typeof error.message === "string"
  );
}

export async function toApiError(response: Response): Promise<ApiError> {
  const headerId = response.headers.get("x-correlation-id") ?? "";
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (isEnvelope(body)) {
    return new ApiError(
      response.status,
      body.error.code,
      body.error.message,
      body.error.correlation_id || headerId,
      body.error.details,
    );
  }
  return new ApiError(
    response.status,
    "UNEXPECTED_RESPONSE",
    "Неожиданный ответ локального сервиса.",
    headerId,
  );
}

type Query = Record<string, string | number | boolean | null | undefined>;

interface RequestOptions {
  query?: Query;
  body?: unknown;
  version?: number;
}

export function buildUrl(baseUrl: string, path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return `${baseUrl}/api/v1${path}${qs ? `?${qs}` : ""}`;
}

export class ApiClient {
  private readonly connection: ApiConnection;
  private readonly fetchImpl: typeof fetch;

  constructor(connection: ApiConnection, fetchImpl: typeof fetch = fetch.bind(globalThis)) {
    this.connection = connection;
    this.fetchImpl = fetchImpl;
  }

  private async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.connection.token) headers.Authorization = `Bearer ${this.connection.token}`;
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (options.version !== undefined) headers["If-Match"] = `"${options.version}"`;
    const init: RequestInit = { method, headers, cache: "no-store", credentials: "omit" };
    if (options.body !== undefined) init.body = JSON.stringify(options.body);

    let response: Response;
    try {
      response = await this.fetchImpl(buildUrl(this.connection.baseUrl, path, options.query), init);
    } catch {
      throw new ApiError(0, "API_UNREACHABLE", "Локальный сервис недоступен.", "");
    }
    if (!response.ok) throw await toApiError(response);
    return (await response.json()) as T;
  }

  health(): Promise<Health> {
    return this.request("GET", "/health");
  }

  listWorkspaces(cursor?: string): Promise<Page<Workspace>> {
    return this.request("GET", "/workspaces", { query: { cursor, limit: 200 } });
  }

  createWorkspace(body: WorkspaceCreate): Promise<Workspace> {
    return this.request("POST", "/workspaces", { body });
  }

  updateWorkspace(id: string, version: number, body: WorkspacePatch): Promise<Workspace> {
    return this.request("PATCH", `/workspaces/${encodeURIComponent(id)}`, { body, version });
  }

  listSuppliers(params: {
    workspaceId: string;
    query?: string;
    includeArchived?: boolean;
    cursor?: string;
  }): Promise<Page<Supplier>> {
    return this.request("GET", "/suppliers", {
      query: {
        workspace_id: params.workspaceId,
        query: params.query,
        include_archived: params.includeArchived,
        cursor: params.cursor,
      },
    });
  }

  createSupplier(body: SupplierCreate): Promise<Supplier> {
    return this.request("POST", "/suppliers", { body });
  }

  updateSupplier(id: string, version: number, body: SupplierPatch): Promise<Supplier> {
    return this.request("PATCH", `/suppliers/${encodeURIComponent(id)}`, { body, version });
  }
}
