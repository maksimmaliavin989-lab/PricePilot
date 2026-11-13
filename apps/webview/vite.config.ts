import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Browser dev mode (`npm run dev` at the repo root): scripts/dev.mjs starts the local API and
// passes its address/token here. The proxy adds the token server-side, so it never reaches
// the browser bundle. Inside Tauri the UI talks to the API directly (see src/api/connection.ts).
const devApiUrl = process.env.PRICEPILOT_DEV_API_URL;
const devApiToken = process.env.PRICEPILOT_DEV_API_TOKEN;

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy:
      devApiUrl && devApiToken
        ? {
            "/api": {
              target: devApiUrl,
              changeOrigin: true,
              headers: { Authorization: `Bearer ${devApiToken}` },
            },
          }
        : undefined,
  },
  build: {
    target: "es2022",
    sourcemap: false,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
