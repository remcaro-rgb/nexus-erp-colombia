// P22c feature-e2e fixtures (Node side). DEV/test only.
//
// The pytest runner (tests/cross_module/qa_harness/e2e.py) builds a synthetic PostgreSQL world, serves the real Django
// app on 127.0.0.1:<ephemeral> and writes a world file (mode 0600, temp dir) whose path arrives in $NEXUS_E2E_WORLD:
//   { "api_origin": "http://127.0.0.1:NNNN", "tokens": {user: bearer}, "user": "...", "augment": bool, "data": {...ids} }
// This fixture starts the P08 Vite dev server (frontend/vite.config.ts, 127.0.0.1, free port) with /api proxied to that
// origin and with frontend/index.html pointing at support/app-main.tsx, and injects the tokens into each page.
// Tokens are synthetic LocalIssuer tokens of a throw-away test tenant; they are never printed.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test as base, expect, type Page } from "@playwright/test";
import type { Plugin, ViteDevServer } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
export const repoRoot = resolve(here, "../../../..");
const frontendDir = resolve(repoRoot, "frontend");
const harnessEntry = resolve(here, "app-main.tsx");

export interface World {
  api_origin: string;
  tokens: Record<string, string>;
  user: string;
  augment?: boolean;
  data: Record<string, any>;
}

export function loadWorld(): World {
  const file = process.env.NEXUS_E2E_WORLD;
  if (!file) throw new Error("NEXUS_E2E_WORLD not set: run the spec through its pytest runner (tests/cross_module/test_xm_e2e_features.py)");
  const w = JSON.parse(readFileSync(file, "utf-8")) as World;
  const host = new URL(w.api_origin).hostname;
  if (!["127.0.0.1", "localhost", "::1"].includes(host)) throw new Error(`refusing non-loopback API origin (DEV-only, D-002)`);
  return w;
}

function harnessHtml(): Plugin {
  return {
    name: "nexus-p22c-e2e-harness",
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        if (!html.includes("/src/app/main.tsx")) throw new Error("frontend/index.html no longer loads /src/app/main.tsx");
        return html.replace("/src/app/main.tsx", `/@fs${harnessEntry}`);
      },
    },
  };
}

async function startVite(apiOrigin: string): Promise<{ server: ViteDevServer; url: string }> {
  process.env.ERP_API_ORIGIN = apiOrigin; // read by frontend/vite.config.ts (proxy target of /api)
  const { createServer } = await import("vite");
  const server = await createServer({
    configFile: resolve(frontendDir, "vite.config.ts"),
    root: frontendDir,
    logLevel: "error",
    plugins: [harnessHtml()],
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      hmr: false,
      fs: { allow: [frontendDir, resolve(repoRoot, "node_modules"), resolve(repoRoot, "docs/spec/ui"), here] },
    },
  });
  await server.listen();
  const url = server.resolvedUrls?.local[0];
  if (!url) throw new Error("vite did not report a local URL");
  return { server, url: url.replace(/\/$/, "") };
}

type WorkerFixtures = { world: World; appUrl: string };
type TestFixtures = { app: App };

export class App {
  constructor(
    readonly page: Page,
    readonly url: string,
    readonly world: World,
  ) {}

  /** Navigate to an app path (e.g. /m/tesoreria/recibos) as `user` (default: the world's user). */
  async open(path: string, user?: string): Promise<void> {
    if (user) await this.as(user, false);
    await this.page.goto(`${this.url}${path}`);
    await expect(this.page.locator("#nx-main, main").first()).toBeVisible();
  }

  /** Public-API client as `user` (same bearer the UI uses). Only for steps the product has no UI for: record them with gap(). */
  api(user?: string) {
    const token = this.world.tokens[user ?? this.world.user];
    const company = this.world.data.company_id as string | undefined;
    const base = this.world.api_origin;
    const request = this.page.request;
    const headers = (write: boolean): Record<string, string> => ({
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      ...(company ? { "X-Company-Id": company } : {}),
      ...(write ? { "Idempotency-Key": crypto.randomUUID() } : {}),
    });
    const parse = async (r: Awaited<ReturnType<typeof request.get>>, what: string) => {
      const text = await r.text();
      if (!r.ok()) throw new Error(`${what} -> HTTP ${r.status()}: ${text.slice(0, 800)}`);
      return text ? JSON.parse(text) : null;
    };
    return {
      get: async (path: string, params?: Record<string, string>) =>
        parse(await request.get(`${base}${path}`, { headers: headers(false), params }), `GET ${path}`),
      post: async (path: string, data?: unknown) =>
        parse(await request.post(`${base}${path}`, { headers: headers(true), data: data ?? {} }), `POST ${path}`),
    };
  }

  /** A step of the acceptance text the product offers no UI for: done through the public API, recorded, and failing the test at the end. */
  async gap<T>(defect: string, what: string, viaApi: () => Promise<T>): Promise<T> {
    this.gaps.push(`${defect}: ${what}`);
    return viaApi();
  }

  readonly gaps: string[] = [];

  /** Switch the signed-in synthetic user (sessionStorage) and optionally reload. */
  async as(user: string, reload = true): Promise<void> {
    if (!this.world.tokens[user]) throw new Error(`no token for ${user}`);
    await this.page.addInitScript((u) => window.sessionStorage.setItem("nx-e2e-user", u), user);
    if (reload && this.page.url().startsWith("http")) {
      await this.page.evaluate((u) => window.sessionStorage.setItem("nx-e2e-user", u), user);
      await this.page.reload();
    }
  }
}

export const test = base.extend<TestFixtures, WorkerFixtures>({
  world: [async ({}, use) => use(loadWorld()), { scope: "worker" }],
  appUrl: [
    async ({ world }, use) => {
      const { server, url } = await startVite(world.api_origin);
      try {
        await use(url);
      } finally {
        await server.close();
      }
    },
    { scope: "worker" },
  ],
  app: async ({ page, appUrl, world }, use, testInfo) => {
    await page.addInitScript((cfg) => {
      (window as any).__NEXUS_E2E__ = cfg;
    }, { tokens: world.tokens, user: world.user, augment: Boolean(world.augment) });
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    const app = new App(page, appUrl, world);
    await use(app);
    expect(errors, `uncaught page errors:\n${errors.join("\n")}`).toEqual([]);
    for (const g of app.gaps) testInfo.annotations.push({ type: "gap", description: g });
    expect(app.gaps, "acceptance steps without a UI (done through the API; filed defects)").toEqual([]);
  },
});

export { expect };

/** Keyboard-only helper: Tab until `target` has focus (max `limit` presses); fails otherwise. */
export async function tabTo(page: Page, target: ReturnType<Page["locator"]>, limit = 60): Promise<void> {
  for (let i = 0; i < limit; i++) {
    if (await target.evaluate((el) => el === document.activeElement).catch(() => false)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`keyboard: could not reach element with Tab in ${limit} presses`);
}

/** Every visible form control has an accessible name (label/aria-label/aria-labelledby). */
export async function expectLabelledControls(page: Page, scope = "main"): Promise<void> {
  const unlabelled = await page.locator(scope).first().evaluate((root) => {
    const out: string[] = [];
    root.querySelectorAll("input:not([type=hidden]), select, textarea, button").forEach((el) => {
      const h = el as HTMLElement;
      if (!h.offsetParent && h.getClientRects().length === 0) return;
      const id = h.getAttribute("id");
      const named =
        h.getAttribute("aria-label") ||
        h.getAttribute("aria-labelledby") ||
        h.getAttribute("title") ||
        (id && root.ownerDocument.querySelector(`label[for="${CSS.escape(id)}"]`)) ||
        h.closest("label") ||
        (h.tagName === "BUTTON" && h.textContent?.trim());
      if (!named) out.push(h.outerHTML.slice(0, 120));
    });
    return out;
  });
  expect(unlabelled, "form controls without an accessible name").toEqual([]);
}
