import { invoke } from "@tauri-apps/api/core";

/** How the UI reaches the local API. */
export interface ApiConnection {
  /** Absolute (Tauri) or same-origin relative (browser dev via Vite proxy) base URL. */
  readonly baseUrl: string;
  /** Per-launch capability token; `null` when a dev proxy injects it server-side. */
  readonly token: string | null;
}

interface ShellConnection {
  base_url: string;
  token: string;
}

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

const RETRY_DELAY_MS = 400;
const MAX_ATTEMPTS = 150; // ~60 s: first start runs migrations

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Inside Tauri the shell spawns the API sidecar on a random loopback port and keeps the token.
 * The UI asks for both through a command (no token in URLs, storage or the bundle).
 */
export async function resolveConnection(): Promise<ApiConnection> {
  if (!isTauri()) {
    return { baseUrl: "", token: null };
  }
  let lastError: unknown = null;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
    try {
      const shell = await invoke<ShellConnection>("backend_connection");
      return { baseUrl: shell.base_url, token: shell.token };
    } catch (error: unknown) {
      lastError = error;
      if (String(error).includes("BACKEND_FAILED")) break;
      await sleep(RETRY_DELAY_MS);
    }
  }
  throw new Error(`Локальный сервис не запустился: ${String(lastError)}`);
}
