import { describe, expect, it } from "vitest";

import { ApiClient, ApiError, buildUrl } from "./client";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

describe("buildUrl", () => {
  it("prefixes the API version and drops empty params", () => {
    expect(buildUrl("", "/suppliers", { workspace_id: "w", query: "", cursor: undefined })).toBe(
      "/api/v1/suppliers?workspace_id=w",
    );
    expect(buildUrl("http://127.0.0.1:5000", "/health")).toBe(
      "http://127.0.0.1:5000/api/v1/health",
    );
  });
});

describe("ApiClient", () => {
  it("sends the bearer token and If-Match version", async () => {
    let captured: Request | null = null;
    const client = new ApiClient(
      { baseUrl: "http://127.0.0.1:5000", token: "secret-token" },
      (input, init) => {
        captured = new Request(input, init);
        return Promise.resolve(jsonResponse(200, { id: "s1" }));
      },
    );
    await client.updateSupplier("s1", 3, { name: "Новое" });
    expect(captured).not.toBeNull();
    const request = captured as unknown as Request;
    expect(request.method).toBe("PATCH");
    expect(request.headers.get("authorization")).toBe("Bearer secret-token");
    expect(request.headers.get("if-match")).toBe('"3"');
    expect(request.credentials).toBe("omit");
  });

  it("omits Authorization when the dev proxy injects it", async () => {
    let auth: string | null = "unset";
    const client = new ApiClient({ baseUrl: "", token: null }, (input, init) => {
      auth = new Request(
        new URL(input instanceof Request ? input.url : input.toString(), "http://localhost"),
        init,
      ).headers.get("authorization");
      return Promise.resolve(jsonResponse(200, { status: "ok" }));
    });
    await client.health();
    expect(auth).toBeNull();
  });

  it("maps the error envelope with correlation id", async () => {
    const client = new ApiClient({ baseUrl: "", token: null }, () =>
      Promise.resolve(
        jsonResponse(409, {
          error: {
            code: "VERSION_CONFLICT",
            message: "Запись уже изменена.",
            details: { current_version: 4 },
            correlation_id: "corr-12345678",
          },
        }),
      ),
    );
    const error = await client.health().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.code).toBe("VERSION_CONFLICT");
    expect(apiError.status).toBe(409);
    expect(apiError.correlationId).toBe("corr-12345678");
    expect(apiError.details).toEqual({ current_version: 4 });
  });

  it("falls back to header correlation id for unexpected bodies", async () => {
    const client = new ApiClient({ baseUrl: "", token: null }, () =>
      Promise.resolve(
        new Response("oops", { status: 502, headers: { "x-correlation-id": "h-1" } }),
      ),
    );
    const error = (await client.health().catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("UNEXPECTED_RESPONSE");
    expect(error.correlationId).toBe("h-1");
  });

  it("reports network failures without leaking details", async () => {
    const client = new ApiClient({ baseUrl: "", token: null }, () =>
      Promise.reject(new TypeError("connect ECONNREFUSED 127.0.0.1:5000")),
    );
    const error = (await client.health().catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("API_UNREACHABLE");
    expect(error.message).not.toContain("127.0.0.1");
  });

  it("re-reads the connection once after a network failure (restarted API)", async () => {
    const seen: string[] = [];
    let calls = 0;
    const client = new ApiClient(
      { baseUrl: "http://127.0.0.1:1111", token: "old" },
      (input, init) => {
        const request = new Request(input, init);
        seen.push(`${request.url} ${request.headers.get("authorization") ?? ""}`);
        calls += 1;
        if (calls === 1) return Promise.reject(new TypeError("connection refused"));
        return Promise.resolve(jsonResponse(200, { status: "ok" }));
      },
      () => Promise.resolve({ baseUrl: "http://127.0.0.1:2222", token: "new" }),
    );
    await client.health();
    expect(seen).toEqual([
      "http://127.0.0.1:1111/api/v1/health Bearer old",
      "http://127.0.0.1:2222/api/v1/health Bearer new",
    ]);
  });
});
