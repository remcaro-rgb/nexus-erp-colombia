// Design tokens -> CSS custom properties (owner P08). Pure functions, shared by the Vite plugin
// (static CSS, no flash on load) and by <TokenScope> (runtime, identical values for every shell variant).
// Source of truth: docs/spec/ui/tokens.json (frozen at G0). Never hand-edit token values here.

export type ThemeName = "light" | "dark";

export interface UiTokens {
  schema: string;
  version: string;
  brand: { placeholder_name: string; logo: { tile_bg: string; icon_color: string; tile_px: number; tile_radius_px: number; tile_shadow: string } };
  font: { family: string; fallback: string; size_px: Record<string, number>; min_text_px: number };
  palette: Record<string, Record<string, string>>;
  color: { light: Record<string, string>; dark: Record<string, string> };
  chart: {
    revenue: { stroke: string; fill: string; stroke_width: number; tension: number; dash: number[] | null; area: boolean };
    expenses: { stroke: string; fill: string; stroke_width: number; tension: number; dash: number[] | null; area: boolean };
    grid_y: string;
    height_px: number;
    legend_box_px: number;
    tick_px: number;
  };
  layout: {
    header_h: number;
    module_bar_h: number;
    sidebar_w: number;
    sidebar_collapsed_w: number;
    ai_panel_w: number;
    dev_banner_h: number;
    header_padding_x: number;
    module_bar_padding_x: number;
    content_padding: { base: number; md: number };
    gap: { kpi_grid: number; section: number };
    card: { radius: number; padding_kpi: number; padding_panel: number; border_w: number; shadow: string };
    control_radius: number;
    pill_radius: number;
    search: { w: number; w_compact: number };
    avatar_px: number;
    z: Record<string, number>;
  };
  breakpoints: Record<string, number>;
  motion: { sidebar_ms: number; ai_panel_ms: number };
}

/** "app.bg" -> "--nx-color-app-bg" */
export function colorVar(key: string): string {
  return `--nx-color-${key.replace(/\./g, "-")}`;
}

/** Theme-independent variables (layout, font, palette, motion). */
export function baseVariables(t: UiTokens): Record<string, string> {
  const v: Record<string, string> = {};
  v["--nx-font-family"] = `"${t.font.family}", ${t.font.fallback}`;
  for (const [k, px] of Object.entries(t.font.size_px)) v[`--nx-text-${k}`] = `${px}px`;
  for (const [hue, shades] of Object.entries(t.palette)) {
    for (const [shade, hex] of Object.entries(shades)) v[`--nx-${hue}-${shade}`] = hex;
  }
  const L = t.layout;
  v["--nx-header-h"] = `${L.header_h}px`;
  v["--nx-module-bar-h"] = `${L.module_bar_h}px`;
  v["--nx-sidebar-w"] = `${L.sidebar_w}px`;
  v["--nx-sidebar-collapsed-w"] = `${L.sidebar_collapsed_w}px`;
  v["--nx-ai-panel-w"] = `${L.ai_panel_w}px`;
  v["--nx-dev-banner-h"] = `${L.dev_banner_h}px`;
  v["--nx-header-px"] = `${L.header_padding_x}px`;
  v["--nx-module-bar-px"] = `${L.module_bar_padding_x}px`;
  v["--nx-content-p"] = `${L.content_padding.base}px`;
  v["--nx-content-p-md"] = `${L.content_padding.md}px`;
  v["--nx-gap-kpi"] = `${L.gap.kpi_grid}px`;
  v["--nx-gap-section"] = `${L.gap.section}px`;
  v["--nx-card-radius"] = `${L.card.radius}px`;
  v["--nx-card-p-kpi"] = `${L.card.padding_kpi}px`;
  v["--nx-card-p-panel"] = `${L.card.padding_panel}px`;
  v["--nx-card-shadow"] = L.card.shadow;
  v["--nx-control-radius"] = `${L.control_radius}px`;
  v["--nx-search-w"] = `${L.search.w}px`;
  v["--nx-search-w-compact"] = `${L.search.w_compact}px`;
  v["--nx-avatar"] = `${L.avatar_px}px`;
  v["--nx-chart-h"] = `${t.chart.height_px}px`;
  v["--nx-brand-tile-bg"] = t.brand.logo.tile_bg;
  v["--nx-brand-tile-shadow"] = t.brand.logo.tile_shadow;
  for (const [k, z] of Object.entries(L.z)) v[`--nx-z-${k.replace(/_/g, "-")}`] = String(z);
  v["--nx-motion-sidebar"] = `${t.motion.sidebar_ms}ms`;
  v["--nx-motion-ai"] = `${t.motion.ai_panel_ms}ms`;
  return v;
}

/** Theme colour variables. */
export function themeVariables(t: UiTokens, theme: ThemeName): Record<string, string> {
  const v: Record<string, string> = {};
  for (const [k, val] of Object.entries(t.color[theme])) v[colorVar(k)] = val;
  return v;
}

/** All variables for a theme (what <TokenScope> applies). */
export function cssVariables(t: UiTokens, theme: ThemeName): Record<string, string> {
  return { ...baseVariables(t), ...themeVariables(t, theme) };
}

function block(selector: string, vars: Record<string, string>): string {
  const body = Object.entries(vars)
    .map(([k, v]) => `  ${k}: ${v};`)
    .join("\n");
  return `${selector} {\n${body}\n}\n`;
}

/** Static stylesheet: light on :root, dark on .dark (class strategy) and on OS preference before JS runs. */
export function tokensToCss(t: UiTokens): string {
  const dark = themeVariables(t, "dark");
  return [
    `/* generated from docs/spec/ui/tokens.json ${t.version} — do not edit */`,
    block(":root", { ...baseVariables(t), ...themeVariables(t, "light") }),
    block(":root.dark", dark),
    `@media (prefers-color-scheme: dark) {\n${block(":root:not(.light):not(.dark)", dark)}}\n`,
    `@media (prefers-reduced-motion: reduce) {\n${block(":root", { "--nx-motion-sidebar": "0ms", "--nx-motion-ai": "0ms" })}}\n`,
  ].join("\n");
}

/** Stable fingerprint of the variables (used to prove that every shell variant uses identical tokens). */
export function tokensFingerprint(vars: Record<string, string>): string {
  const s = Object.keys(vars)
    .sort()
    .map((k) => `${k}=${vars[k]}`)
    .join(";");
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}
