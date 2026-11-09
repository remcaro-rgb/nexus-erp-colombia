/// <reference types="vitest/config" />
// Vite + Vitest configuration (owner P08). DEV-only. No CDN, no remote fonts: everything is bundled locally.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tokensToCss, type UiTokens } from "./src/design-system/tokens/css";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..");
const tokensPath = resolve(repoRoot, "docs/spec/ui/tokens.json");

/** `virtual:nexus-tokens.css` — CSS custom properties generated from docs/spec/ui/tokens.json (single source of truth). */
function nexusTokens(): Plugin {
  const id = "virtual:nexus-tokens.css";
  const resolved = "\0" + id;
  return {
    name: "nexus-tokens",
    resolveId(source) {
      return source === id ? resolved : null;
    },
    load(source) {
      if (source !== resolved) return null;
      this.addWatchFile(tokensPath);
      const tokens = JSON.parse(readFileSync(tokensPath, "utf-8")) as UiTokens;
      return tokensToCss(tokens);
    },
  };
}

const devPort = Number(process.env.ERP_FRONTEND_PORT ?? 5173);
const apiTarget = process.env.ERP_API_ORIGIN ?? `http://127.0.0.1:${process.env.ERP_DEV_PORT ?? 8080}`;

export default defineConfig({
  plugins: [nexusTokens(), react(), tailwindcss()],
  resolve: {
    alias: {
      "@app": resolve(here, "src/app"),
      "@shell": resolve(here, "src/shell"),
      "@ds": resolve(here, "src/design-system"),
      "@lib": resolve(here, "src/lib"),
      "@i18n": resolve(here, "src/i18n"),
      "@features": resolve(here, "src/features"),
      "@spec": resolve(repoRoot, "docs/spec"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: devPort,
    strictPort: false,
    fs: { allow: [here, resolve(repoRoot, "docs/spec/ui")] },
    proxy: {
      "/api": { target: apiTarget, changeOrigin: false },
    },
  },
  preview: { host: "127.0.0.1", port: Number(process.env.ERP_PREVIEW_PORT ?? 4173) },
  build: {
    target: "es2022",
    sourcemap: true,
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router"],
          charts: ["chart.js"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // Unit tests live in __tests__ folders and end in .unit.ts(x) so that a config-less `playwright test`
    // (Makefile test-ids) never picks them up.
    include: ["src/**/__tests__/**/*.unit.{ts,tsx}"],
    setupFiles: ["src/app/test/setup.ts"],
    css: false,
    testTimeout: 30_000,
    restoreMocks: true,
  },
});
