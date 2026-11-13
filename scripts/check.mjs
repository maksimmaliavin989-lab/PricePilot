#!/usr/bin/env node
// Runs the same checks as CI. Usage: node scripts/check.mjs [backend|webview|desktop|contracts]...
import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// rustup installs to ~/.cargo/bin; find it even if the shell profile was not updated.
process.env.PATH = [process.env.PATH, join(homedir(), ".cargo", "bin")].join(
  delimiter,
);

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const backend = join(root, "backend");
const webview = join(root, "apps/webview");
const tauri = join(root, "apps/desktop/src-tauri");

const suites = {
  backend: [
    ["uv", ["run", "ruff", "format", "--check", "."], backend],
    ["uv", ["run", "ruff", "check", "."], backend],
    ["uv", ["run", "mypy"], backend],
    ["uv", ["run", "pytest", "-q"], backend],
  ],
  contracts: [
    [
      "uv",
      [
        "run",
        "python",
        "-m",
        "pricepilot",
        "openapi",
        "--check",
        "../contracts/openapi/openapi.json",
      ],
      backend,
    ],
    ["npm", ["run", "check:api"], webview],
  ],
  webview: [
    ["npm", ["run", "format:check"], webview],
    ["npm", ["run", "lint"], webview],
    ["npm", ["run", "typecheck"], webview],
    ["npm", ["test"], webview],
    ["npm", ["run", "build"], webview],
  ],
  desktop: [
    ["cargo", ["fmt", "--check"], tauri],
    ["cargo", ["clippy", "--all-targets", "--", "-D", "warnings"], tauri],
  ],
};

const selected = process.argv.slice(2);
const names = selected.length
  ? selected
  : ["backend", "contracts", "webview", "desktop"];
const failures = [];
for (const name of names) {
  const steps = suites[name];
  if (!steps) {
    console.error(`unknown suite: ${name}`);
    process.exit(2);
  }
  for (const [cmd, args, cwd] of steps) {
    console.log(`\n▶ [${name}] ${cmd} ${args.join(" ")}`);
    const result = spawnSync(cmd, args, { cwd, stdio: "inherit" });
    if (result.error?.code === "ENOENT") {
      console.error(
        `✖ ${cmd} not found — install it to run the ${name} checks`,
      );
      failures.push(`${name}: ${cmd} missing`);
      break;
    }
    if (result.status !== 0) failures.push(`${name}: ${cmd} ${args.join(" ")}`);
  }
}
if (failures.length) {
  console.error(`\n✖ Failed:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
console.log("\n✔ All checks passed");
