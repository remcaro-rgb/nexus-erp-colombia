// ui_snapshot.cjs — browser part of tools/ui_snapshot.py (integrator-only). Reads one JSON job on stdin, prints one JSON result line.
// System Chrome only (executablePath), headless, no devtools. Credentials come on stdin and are never printed.
"use strict";
const path = require("path");
const fs = require("fs");

const MASK = 'input[type=password], input[name*="token" i], input[name*="secret" i], input[autocomplete="one-time-code"], [data-sensitive], [data-testid*="token" i], [data-testid*="secret" i]';
const NOT_FOUND = /(^|\b)(404|no encontrad[ao]|página no existe|not found|no implementad[ao]|not implemented)(\b|$)/i;

async function measure(page) {
  return page.evaluate(() => {
    const box = (el) => (el ? Math.round(el.getBoundingClientRect().width) + "x" + Math.round(el.getBoundingClientRect().height) : null);
    const header = document.querySelector("header");
    const asides = Array.from(document.querySelectorAll("aside, nav[aria-label]"));
    const side = asides.find((a) => /m[oó]dulos/i.test(a.textContent || "") || a.getBoundingClientRect().left < 5) || null;
    const ai = asides.find((a) => /asistente ia/i.test(a.textContent || "")) || null;
    return { header: header ? Math.round(header.getBoundingClientRect().height) : null, sidebar: side ? Math.round(side.getBoundingClientRect().width) : null,
             ai: ai ? Math.round(ai.getBoundingClientRect().width) : null, dark: document.documentElement.classList.contains("dark"), _b: box(header) };
  });
}

function notes(m, variant) {
  const n = [];
  n.push(m.header == null ? "header not found" : m.header === 64 ? "header 64 ✓" : `header ${m.header} (contract 64)`);
  const sw = variant === "collapsed" ? 80 : 256;
  n.push(m.sidebar == null ? "sidebar not found" : m.sidebar === sw ? `sidebar ${sw} ✓` : `sidebar ${m.sidebar} (contract ${sw})`);
  if (variant === "collapsed") n.push(m.ai == null || m.ai === 0 ? "AI panel closed ✓" : `AI panel ${m.ai} (expected closed)`);
  else n.push(m.ai == null ? "AI panel not found" : m.ai === 320 ? "AI panel 320 ✓" : `AI panel ${m.ai} (contract 320)`);
  if (variant === "dark") n.push(m.dark ? "dark class ✓" : "dark class missing on <html>");
  return n.join("; ");
}

async function setTheme(page, theme) {
  await page.emulateMedia({ colorScheme: theme });
  await page.evaluate((t) => { document.documentElement.classList.toggle("dark", t === "dark"); }, theme);
  await page.waitForTimeout(300);
}

async function main() {
  const job = JSON.parse(fs.readFileSync(0, "utf8"));
  const pw = require(require.resolve(job.module, { paths: [job.module_dir, path.join(job.module_dir, "node_modules")] }));
  const chromium = pw.chromium;
  const res = { shots: [], not_built: [], error: null };
  const browser = await chromium.launch({ executablePath: job.chrome, headless: true, devtools: false });
  try {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: job.dsf, locale: "es-CO", timezoneId: "America/Bogota", colorScheme: "light" });
    const page = await ctx.newPage();
    const shot = async (id, label, route, variant) => {
      const file = `${id}-${variant}.png`;
      await page.screenshot({ path: path.join(job.outdir, file), mask: [page.locator(MASK)], animations: "disabled" });
      res.shots.push({ label, route, variant, status: "captured", file, notes: notes(await measure(page), variant) });
    };
    await page.goto(job.base + "/", { waitUntil: "networkidle", timeout: 60000 });
    for (const th of ["light", "dark"]) { await setTheme(page, th); await shot("login", "Login / entry", "/", th); }
    await setTheme(page, "light");
    if (job.login) {
      // The SPA either shows the identity-provider form directly, offers a sign-in button, or redirects there from a protected route.
      const USER = '#username, input[name="username"], input[type="email"]';
      if (!(await page.locator(USER).count())) {
        const btn = page.getByRole("button", { name: /ingresar|iniciar sesi[oó]n|entrar|acceder|sign in|log ?in/i })
          .or(page.getByRole("link", { name: /ingresar|iniciar sesi[oó]n|entrar|acceder|sign in|log ?in/i })).first();
        if (await btn.count()) await btn.click().catch(() => {});
        else await page.goto(job.base + "/m/inicio", { waitUntil: "networkidle", timeout: 45000 }).catch(() => {});
        await page.locator(USER).first().waitFor({ timeout: 20000 }).catch(() => {});
      }
      const user = page.locator(USER).first();
      if (await user.count()) {
        await user.fill(job.login.user);
        await page.locator('#password, input[name="password"], input[type="password"]').first().fill(job.login.password);
        await Promise.all([page.waitForLoadState("networkidle").catch(() => {}), page.locator('#kc-login, button[type="submit"], input[type="submit"]').first().click()]);
        await page.waitForURL((u) => !/\/auth\/realms\//.test(u.pathname), { timeout: 30000 }).catch(() => {});
        await page.waitForTimeout(1500);
        if (/\/auth\/realms\//.test(new URL(page.url()).pathname)) {
          const why = ((await page.locator("#input-error, .kc-feedback-text, [id*=error]").first().textContent().catch(() => "")) || "").trim().slice(0, 80);
          res.error = "login failed: still on the identity provider page after submitting" + (why ? ` (${why})` : "");
          res.login = "failed";
        } else res.login = "ok";
      } else {
        res.error = "login form not found (no sign-in button, and /m/inicio did not redirect to the identity provider)";
        res.login = "no-form";
      }
    } else {
      res.error = "no demo login available: only the entry screen was captured";
    }
    if (job.login && res.login === "ok") {
      for (const pg of job.pages) {
        let status = 0;
        try {
          const r = await page.goto(job.base + pg.route, { waitUntil: "networkidle", timeout: 45000 });
          status = r ? r.status() : 0;
        } catch (e) { res.not_built.push({ label: pg.label, route: pg.route, status: "not yet built (navigation failed)" }); continue; }
        const heading = (await page.locator("main h1, main h2, [role=main] h1, h1").first().textContent().catch(() => "")) || "";
        if (status >= 400 || NOT_FOUND.test(heading) || /\/(login|auth)\b/.test(new URL(page.url()).pathname) && pg.id !== "inicio") {
          res.not_built.push({ label: pg.label, route: pg.route, status: `not yet built (HTTP ${status}${heading ? ", heading: " + heading.trim().slice(0, 40) : ""})` });
          continue;
        }
        for (const th of ["light", "dark"]) { await setTheme(page, th); await shot(pg.id, pg.label, pg.route, th); }
        await setTheme(page, "light");
      }
      // dashboard with the sidebar and the AI panel collapsed
      try {
        await page.goto(job.base + "/m/inicio", { waitUntil: "networkidle", timeout: 45000 });
        const side = page.locator('button[aria-label*="contraer" i], button[aria-label*="collapse" i], button[aria-controls*="sidebar" i][aria-expanded="true"]').first();
        const ai = page.locator('button[aria-label*="cerrar asistente" i], button[aria-label*="close ai" i], button[aria-controls*="ai" i][aria-expanded="true"]').first();
        let found = 0;
        if (await side.count()) { await side.click(); found++; }
        if (await ai.count()) { await ai.click(); found++; }
        await page.waitForTimeout(500);
        await shot("inicio", "Inicio (sidebar + AI panel collapsed)", "/m/inicio", "collapsed");
        if (found < 2) res.shots[res.shots.length - 1].notes += "; collapse control(s) not found (aria-label/aria-controls), captured as rendered";
      } catch (e) { res.shots.push({ label: "Inicio collapsed", route: "/m/inicio", variant: "collapsed", status: "failed: " + String(e.message).slice(0, 120), file: null }); }
    }
  } catch (e) {
    res.error = String(e && e.message ? e.message : e).slice(0, 300);
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(res) + "\n");
}
main().catch((e) => { process.stdout.write(JSON.stringify({ shots: [], not_built: [], error: String(e.message || e).slice(0, 300) }) + "\n"); });
