// Chart panel (owner P08). ADR P08-ADR-1: Chart.js (D-003), pinned, bundled locally, animation off under reduced motion.
// Accessible alternative: a summary sentence + a data-table toggle (UI-CONTRACT §15). Series styles from tokens.chart.
import { useEffect, useRef, useState } from "react";
import { CategoryScale, Chart, Filler, Legend, LinearScale, LineController, LineElement, PointElement, Tooltip as ChartTooltip } from "chart.js";
import type { PanelResponse } from "@lib/api/types";
import { tokens } from "../tokens";
import { useT } from "@i18n/index";
import { Dec } from "@lib/money";
import { formatUnit } from "./format";
import { Skeleton, EmptyState } from "./Feedback";

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Filler, Legend, ChartTooltip);

function reducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true;
  }
}

export function ChartPanel({ title, data, unit = "COP", loading }: { title: string; data: PanelResponse | null; unit?: string; loading?: boolean }) {
  const t = useT();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [asTable, setAsTable] = useState(false);
  const hasData = Boolean(data && data.state === "ok" && data.points.length > 0);

  useEffect(() => {
    if (!hasData || asTable || !canvas.current || !data) return;
    const ctx = canvas.current.getContext?.("2d");
    if (!ctx) return;
    const styles = [tokens.chart.revenue, tokens.chart.expenses];
    const chart = new Chart(ctx, {
      type: "line",
      data: {
        labels: data.points.map((p) => p.label),
        datasets: data.series.map((s, i) => {
          const st = styles[i] ?? tokens.chart.expenses;
          return {
            label: s.label,
            // Chart.js needs numbers for drawing only; the table and summary use the exact decimal strings.
            data: data.points.map((p) => (p.values[s.id] == null ? null : new Dec(p.values[s.id] as string).toNumber())),
            borderColor: st.stroke,
            backgroundColor: st.fill,
            borderWidth: st.stroke_width,
            borderDash: st.dash ?? [],
            fill: st.area,
            tension: st.tension,
            pointRadius: 0,
          };
        }),
      },
      options: {
        animation: reducedMotion() ? false : undefined,
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { boxWidth: tokens.chart.legend_box_px, font: { size: tokens.chart.tick_px } } } },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: tokens.chart.tick_px } } },
          y: { grid: { color: tokens.chart.grid_y }, ticks: { font: { size: tokens.chart.tick_px } } },
        },
      },
    });
    return () => chart.destroy();
  }, [data, hasData, asTable]);

  return (
    <section className="nx-card flex flex-col p-6" aria-label={title}>
      <div className="mb-4 flex items-center justify-between gap-2">
        <h3 className="text-base font-bold text-fg">{title}</h3>
        {hasData ? (
          <button type="button" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)} className="min-h-8 rounded-control px-2 text-xs font-semibold text-brand hover:bg-nav-hover">
            {asTable ? t("chart.showChart") : t("chart.showTable")}
          </button>
        ) : null}
      </div>
      {loading ? (
        <Skeleton className="h-72 w-full" label={t("dashboard.loading")} />
      ) : !hasData || !data ? (
        <EmptyState title={t("chart.noData")} />
      ) : (
        <>
          <p className="nx-sr-only">{t("chart.summary", { title, count: data.points.length })}</p>
          {asTable ? (
            <div className="max-h-72 overflow-auto">
              <table className="w-full text-sm">
                <caption className="nx-sr-only">{title}</caption>
                <thead>
                  <tr>
                    <th scope="col" className="px-2 py-1 text-left text-xs text-muted" />
                    {data.series.map((s) => (
                      <th key={s.id} scope="col" className="px-2 py-1 text-right text-xs font-semibold text-muted">
                        {s.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.points.map((p) => (
                    <tr key={p.label} className="border-t border-line-subtle">
                      <th scope="row" className="px-2 py-1 text-left font-medium text-body">
                        {p.label}
                      </th>
                      {data.series.map((s) => (
                        <td key={s.id} className="px-2 py-1 text-right tabular-nums">
                          {p.values[s.id] == null ? "—" : formatUnit(unit, p.values[s.id] as string)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="relative h-72">
              <canvas ref={canvas} role="img" aria-label={t("chart.summary", { title, count: data.points.length })} />
            </div>
          )}
        </>
      )}
    </section>
  );
}
