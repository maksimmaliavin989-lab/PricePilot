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
export type SourceDocument = Schemas["DocumentOut"];
export type ImportResult = Schemas["ImportOut"];
export type ProcessingRun = Schemas["ProcessingRunOut"];
export type Line = Schemas["LineOut"];
export type Field = Schemas["FieldOut"];
export type FieldRevision = Schemas["FieldRevisionOut"];
export type SourceWindow = Schemas["SourceWindowOut"];
export type Unit = Schemas["UnitOut"];
export type Backup = Schemas["BackupOut"];
export type Product = Schemas["ProductOut"];
export type Offer = Schemas["OfferOut"];
export type OfferImport = Schemas["OfferImportOut"];
export type Candidate = Schemas["CandidateOut"];
export type Candidates = Schemas["CandidatesOut"];
export type CostPolicy = Schemas["CostPolicyOut"];
export type ComponentType = Schemas["ComponentTypeIO"];
export type FxRate = Schemas["FxRateOut"];
export type FxRateIn = Schemas["FxRateIn"];
export type TaxRateSource = Schemas["TaxRateSourceOut"];
export type TaxRateSourceIn = Schemas["TaxRateSourceIn"];
export type Comparison = Schemas["ComparisonOut"];
export type ComparisonSummary = Schemas["ComparisonSummaryOut"];
export type ComparisonItem = Schemas["ComparisonItemOut"];
export type ComparisonOffer = Schemas["ComparisonOfferOut"];
export type OfferTerms = Schemas["OfferTermsIO"];
export type CostComponentIn = Schemas["ComponentIO"];
export type CostSnapshot = Schemas["CostSnapshotOut"];
export type CostSnapshotDetail = Schemas["CostSnapshotDetailOut"];
export type MatchDecisionKind = Schemas["MatchDecisionIn"]["decision"];
export type DocumentStatus = SourceDocument["status"];
export type RunStatus = ProcessingRun["status"];
export type ReviewStatus = Field["review_status"];
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
  /** Raw request body (file upload); sent as-is with the given headers. */
  raw?: { data: Blob; headers: Record<string, string> };
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
  private connection: ApiConnection;
  private readonly fetchImpl: typeof fetch;
  private readonly reconnect: (() => Promise<ApiConnection>) | undefined;

  /**
   * @param reconnect Re-reads the API address/token (desktop: the shell restarts a crashed
   *   API with a new port and token). Used once per request after a network failure, which
   *   means the request never reached the API, so repeating it is safe for every method.
   */
  constructor(
    connection: ApiConnection,
    fetchImpl: typeof fetch = fetch.bind(globalThis),
    reconnect?: () => Promise<ApiConnection>,
  ) {
    this.connection = connection;
    this.fetchImpl = fetchImpl;
    this.reconnect = reconnect;
  }

  private async send(path: string, options: RequestOptions, init: RequestInit): Promise<Response> {
    const headers = { ...(init.headers as Record<string, string>) };
    if (this.connection.token) headers.Authorization = `Bearer ${this.connection.token}`;
    return this.fetchImpl(buildUrl(this.connection.baseUrl, path, options.query), {
      ...init,
      headers,
    });
  }

  private async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (options.version !== undefined) headers["If-Match"] = `"${options.version}"`;
    if (options.raw) Object.assign(headers, options.raw.headers);
    const init: RequestInit = { method, headers, cache: "no-store", credentials: "omit" };
    if (options.body !== undefined) init.body = JSON.stringify(options.body);
    if (options.raw) init.body = options.raw.data;

    let response: Response;
    try {
      response = await this.send(path, options, init);
    } catch {
      if (!this.reconnect) {
        throw new ApiError(0, "API_UNREACHABLE", "Локальный сервис недоступен.", "");
      }
      try {
        this.connection = await this.reconnect();
        response = await this.send(path, options, init);
      } catch {
        throw new ApiError(
          0,
          "API_UNREACHABLE",
          "Локальный сервис недоступен. Он перезапускается — повторите действие.",
          "",
        );
      }
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

  // --- Documents and processing --------------------------------------------------------

  listDocuments(workspaceId: string, cursor?: string): Promise<Page<SourceDocument>> {
    return this.request("GET", "/documents", {
      query: { workspace_id: workspaceId, cursor, limit: 200 },
    });
  }

  getDocument(id: string): Promise<SourceDocument> {
    return this.request("GET", `/documents/${encodeURIComponent(id)}`);
  }

  /** Uploads the file bytes as the request body; the name travels percent-encoded. */
  uploadDocument(
    file: Blob & { name: string },
    params: { workspaceId: string; supplierId?: string; source: "file_picker" | "drag_drop" },
  ): Promise<ImportResult> {
    return this.request("POST", "/documents", {
      query: {
        workspace_id: params.workspaceId,
        supplier_id: params.supplierId,
        source: params.source,
      },
      raw: {
        data: file,
        headers: {
          "Content-Type": "application/octet-stream",
          "X-File-Name": encodeURIComponent(file.name),
        },
      },
    });
  }

  reprocessDocument(id: string): Promise<ProcessingRun> {
    return this.request("POST", `/documents/${encodeURIComponent(id)}/processing-runs`);
  }

  getRun(id: string): Promise<ProcessingRun> {
    return this.request("GET", `/processing-runs/${encodeURIComponent(id)}`);
  }

  cancelRun(id: string): Promise<ProcessingRun> {
    return this.request("POST", `/processing-runs/${encodeURIComponent(id)}/cancel`);
  }

  retryRun(id: string): Promise<ProcessingRun> {
    return this.request("POST", `/processing-runs/${encodeURIComponent(id)}/retry`);
  }

  async listAllLines(documentId: string, runId?: string): Promise<Line[]> {
    const lines: Line[] = [];
    let cursor: string | undefined;
    do {
      const page: Page<Line> = await this.request(
        "GET",
        `/documents/${encodeURIComponent(documentId)}/lines`,
        { query: { run_id: runId, cursor, limit: 200 } },
      );
      lines.push(...page.items);
      cursor = page.next_cursor ?? undefined;
    } while (cursor);
    return lines;
  }

  sourceRows(
    documentId: string,
    params: { sheet: string | null; from: number; to: number },
  ): Promise<SourceWindow> {
    return this.request("GET", `/documents/${encodeURIComponent(documentId)}/source-rows`, {
      query: { sheet: params.sheet, from: params.from, to: params.to },
    });
  }

  // --- Review ----------------------------------------------------------------------------

  correctField(
    id: string,
    body: { value: Record<string, string>; reason: string | null; expected_revision: number },
  ): Promise<Field> {
    return this.request("PATCH", `/extracted-fields/${encodeURIComponent(id)}`, { body });
  }

  decideField(
    id: string,
    body: { decision: "accept" | "reject"; reason: string | null; expected_revision: number },
  ): Promise<Field> {
    return this.request("POST", `/extracted-fields/${encodeURIComponent(id)}/decision`, { body });
  }

  fieldRevisions(id: string): Promise<FieldRevision[]> {
    return this.request("GET", `/extracted-fields/${encodeURIComponent(id)}/revisions`);
  }

  listUnits(): Promise<Unit[]> {
    return this.request("GET", "/units");
  }

  // --- Backups ---------------------------------------------------------------------------

  listBackups(): Promise<Backup[]> {
    return this.request("GET", "/backups");
  }

  createBackup(): Promise<Backup> {
    return this.request("POST", "/backups");
  }

  // --- Catalog, offers and matching (TASK_006) -------------------------------------------

  async listAllProducts(workspaceId: string): Promise<Product[]> {
    const items: Product[] = [];
    let cursor: string | undefined;
    do {
      const page: Page<Product> = await this.request("GET", "/products", {
        query: { workspace_id: workspaceId, cursor, limit: 200 },
      });
      items.push(...page.items);
      cursor = page.next_cursor ?? undefined;
    } while (cursor);
    return items;
  }

  createOffers(documentId: string): Promise<OfferImport> {
    return this.request("POST", `/documents/${encodeURIComponent(documentId)}/offers`);
  }

  async listAllOffers(workspaceId: string, documentId?: string): Promise<Offer[]> {
    const items: Offer[] = [];
    let cursor: string | undefined;
    do {
      const page: Page<Offer> = await this.request("GET", "/offers", {
        query: { workspace_id: workspaceId, document_id: documentId, cursor, limit: 200 },
      });
      items.push(...page.items);
      cursor = page.next_cursor ?? undefined;
    } while (cursor);
    return items;
  }

  setOfferPack(
    id: string,
    version: number,
    content: { value: string; unit: string },
  ): Promise<Offer> {
    return this.request("PUT", `/offers/${encodeURIComponent(id)}/pack`, {
      body: { content },
      version,
    });
  }

  offerCandidates(id: string): Promise<Candidates> {
    return this.request("GET", `/offers/${encodeURIComponent(id)}/candidates`);
  }

  decideMatch(
    id: string,
    body: { decision: MatchDecisionKind; product_id: string; reason: string | null },
  ): Promise<Offer> {
    return this.request("POST", `/offers/${encodeURIComponent(id)}/match-decisions`, { body });
  }

  productFromOffer(
    id: string,
    body: { canonical_name?: string; base_unit_code?: string },
  ): Promise<Product> {
    return this.request("POST", `/offers/${encodeURIComponent(id)}/product`, { body });
  }

  // --- Landed cost -------------------------------------------------------------------------

  costPolicy(workspaceId: string): Promise<CostPolicy> {
    return this.request("GET", `/workspaces/${encodeURIComponent(workspaceId)}/cost-policy`);
  }

  saveCostPolicy(
    workspaceId: string,
    body: { name: string; types: ComponentType[]; expected_version: number },
  ): Promise<CostPolicy> {
    return this.request("PUT", `/workspaces/${encodeURIComponent(workspaceId)}/cost-policy`, {
      body,
    });
  }

  listFxRates(workspaceId: string): Promise<FxRate[]> {
    return this.request("GET", `/workspaces/${encodeURIComponent(workspaceId)}/fx-rates`);
  }

  addFxRate(workspaceId: string, body: FxRateIn): Promise<FxRate> {
    return this.request("POST", `/workspaces/${encodeURIComponent(workspaceId)}/fx-rates`, {
      body,
    });
  }

  listTaxRateSources(workspaceId: string): Promise<TaxRateSource[]> {
    return this.request("GET", `/workspaces/${encodeURIComponent(workspaceId)}/tax-rate-sources`);
  }

  addTaxRateSource(workspaceId: string, body: TaxRateSourceIn): Promise<TaxRateSource> {
    return this.request("POST", `/workspaces/${encodeURIComponent(workspaceId)}/tax-rate-sources`, {
      body,
    });
  }

  listComparisons(workspaceId: string): Promise<Page<ComparisonSummary>> {
    return this.request("GET", "/comparisons", {
      query: { workspace_id: workspaceId, limit: 200 },
    });
  }

  createComparison(body: {
    workspace_id: string;
    name: string;
    target_currency: string;
    as_of: string;
  }): Promise<Comparison> {
    return this.request("POST", "/comparisons", { body });
  }

  getComparison(id: string): Promise<Comparison> {
    return this.request("GET", `/comparisons/${encodeURIComponent(id)}`);
  }

  addComparisonItem(
    id: string,
    body: { product_id: string; required_quantity: { value: string; unit: string } },
  ): Promise<Comparison> {
    return this.request("POST", `/comparisons/${encodeURIComponent(id)}/items`, { body });
  }

  addComparisonOffer(itemId: string, body: OfferTerms & { offer_id: string }): Promise<Comparison> {
    return this.request("POST", `/comparison-items/${encodeURIComponent(itemId)}/offers`, {
      body,
    });
  }

  updateComparisonTerms(comparisonOfferId: string, body: OfferTerms): Promise<Comparison> {
    return this.request(
      "PUT",
      `/comparison-offers/${encodeURIComponent(comparisonOfferId)}/terms`,
      { body },
    );
  }

  calculateComparison(id: string): Promise<Comparison> {
    return this.request("POST", `/comparisons/${encodeURIComponent(id)}/calculate`);
  }
}
