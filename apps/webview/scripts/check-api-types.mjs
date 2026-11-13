// Fails when src/api/schema.d.ts is not generated from the current OpenAPI contract.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const cli = join(dirname(require.resolve("openapi-typescript/package.json")), "bin", "cli.js");
const dir = mkdtempSync(join(tmpdir(), "pricepilot-api-"));
const fresh = join(dir, "schema.d.ts");
try {
  execFileSync(process.execPath, [cli, "../../contracts/openapi/openapi.json", "-o", fresh], {
    stdio: "ignore",
  });
  if (readFileSync(fresh, "utf8") !== readFileSync("src/api/schema.d.ts", "utf8")) {
    console.error("src/api/schema.d.ts is stale: run `npm run api:openapi` at the repo root");
    process.exit(1);
  }
} finally {
  rmSync(dir, { recursive: true, force: true });
}
