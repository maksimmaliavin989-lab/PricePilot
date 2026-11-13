#!/usr/bin/env node
// Browser dev mode: start the local API exactly like the Tauri shell does (loopback, random
// port, token via stdin), then start Vite with a proxy that injects the token server-side.
// Data lives in ./.pricepilot-dev (gitignored). Nothing here talks to external services.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dataDir = join(root, ".pricepilot-dev");
const uiOrigins = ["http://127.0.0.1:5173", "http://localhost:5173"];
mkdirSync(dataDir, { recursive: true });

const token = randomBytes(32).toString("base64url");
const api = spawn(
  "uv",
  [
    "run",
    "--project",
    join(root, "backend"),
    "python",
    "-m",
    "pricepilot",
    "serve",
    "--port",
    "0",
    "--token-stdin",
    "--data-dir",
    dataDir,
    ...uiOrigins.flatMap((o) => ["--allow-origin", o]),
  ],
  { cwd: root, stdio: ["pipe", "pipe", "inherit"] },
);
// Keep stdin open: closing it tells the API to shut down.
api.stdin.write(`${token}\n`);

let ui = null;
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  ui?.kill("SIGTERM");
  api.stdin.end();
  setTimeout(() => {
    api.kill("SIGTERM");
    process.exit(code);
  }, 3000).unref();
  api.once("exit", () => process.exit(code));
}
process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));
api.on("exit", (code) => {
  if (!stopping) {
    console.error(`[dev] API exited with code ${code}`);
    stop(1);
  }
});

const lines = createInterface({ input: api.stdout });
lines.on("line", (line) => {
  let event;
  try {
    event = JSON.parse(line);
  } catch {
    return;
  }
  if (event.event === "error") {
    console.error(`[dev] API refused to start: ${event.code}`);
    stop(1);
    return;
  }
  if (event.event !== "ready" || ui) return;
  const apiUrl = `http://127.0.0.1:${event.port}`;
  console.log(
    `[dev] API ready on ${apiUrl} (loopback only). UI: ${uiOrigins[0]}`,
  );
  ui = spawn("npm", ["run", "dev", "--workspace", "@pricepilot/webview"], {
    cwd: root,
    stdio: "inherit",
    env: {
      ...process.env,
      PRICEPILOT_DEV_API_URL: apiUrl,
      PRICEPILOT_DEV_API_TOKEN: token,
    },
  });
  ui.on("exit", (code) => stop(code ?? 0));
});
