// KPI card (owner P08) — UI-CONTRACT §7: radius 16, border, p-20, label 12 px uppercase, value 24 px bold, delta line,
// formula in an info tooltip, drill link. States: loading · ok · no_data · not_available · forbidden_field (omitted) · error.
// Never renders static, sample or random numbers: values come only from the KPI API.
import { Info, RefreshCw } from "lucide-react";
import { Link } from "react-router";
import type { ReactNode } from "react";
import type { KpiValue, RegistryKpi } from "@lib/api/types";
import { cn } from "@lib/cn";
import { useT } from "@i18n/index";
import { Tooltip } from "./Tooltip";
import { Skeleton } from "./Feedback";
import { formatDelta, formatUnit } from "./format";

export interface KpiCardProps {
  def: RegistryKpi;
  value?: KpiValue | null;
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
  icon?: ReactNode;
  drillHref?: string | null;
}

export function KpiCard({ def, value, loading, error, onRetry, icon, drillHref }: KpiCardProps) {
  const t = useT();
  if (value?.state === "forbidden_field") return null;
  const labelId = `kpi-${def.id}-label`;
  let body: ReactNode;
  if (loading) {
    body = <Skeleton className="mt-2 h-8 w-32" label={t("dashboard.loading")} />;
  } else if (error) {
    body = (
      <div className="mt-2 flex items-center gap-2">
        <p className="text-sm text-danger">{t("kpi.error")}</p>
        {onRetry ? (
          <button type="button" onClick={onRetry} className="inline-flex min-h-8 items-center gap-1 rounded-control px-2 text-xs font-semibold text-brand hover:bg-nav-hover">
            <RefreshCw aria-hidden="true" size={12} />
            {t("app.retry")}
          </button>
        ) : null}
      </div>
    );
  } else if (!value || value.state === "not_available") {
    body = <p className="mt-2 text-sm font-medium text-muted">{t("kpi.notAvailable")}</p>;
  } else if (value.state === "no_data" || value.value === null) {
    body = <p className="mt-2 text-sm font-medium text-muted">{t("kpi.noData")}</p>;
  } else {
    const full = formatUnit(value.unit || def.unit, value.value);
    const delta = def.comparison && def.comparison !== "none" ? formatDelta(value.delta) : null;
    body = (
      <>
        <p className="mt-2 text-2xl font-bold tabular-nums text-fg">{full}</p>
        {def.comparison && def.comparison !== "none" ? (
          <p className="mt-1 text-xs">
            <span
              className={cn(
                "font-semibold",
                delta?.tone === "positive" && "text-positive",
                delta?.tone === "negative" && "text-danger",
                (!delta || delta.tone === "neutral") && "text-muted",
              )}
            >
              {delta ? delta.text : t("kpi.noComparison")}
            </span>{" "}
            <span className="text-muted">{t("kpi.vsPrevious")}</span>
          </p>
        ) : null}
      </>
    );
  }
  const card = (
    <section aria-labelledby={labelId} className="nx-card flex flex-col p-5" data-kpi={def.id} data-state={loading ? "loading" : error ? "error" : (value?.state ?? "not_available")}>
      <div className="flex items-start justify-between gap-2">
        <h3 id={labelId} className="text-xs font-semibold uppercase tracking-wider text-muted">
          {def.label}
        </h3>
        <div className="flex items-center gap-1">
          <Tooltip content={t("kpi.formula", { formula: def.formula })} side="top">
            <button type="button" aria-label={t("kpi.formula", { formula: def.formula })} className="inline-flex min-h-6 min-w-6 items-center justify-center rounded-control text-muted hover:bg-nav-hover">
              <Info aria-hidden="true" size={14} />
            </button>
          </Tooltip>
          {icon ? (
            <span aria-hidden="true" className="rounded-control bg-brand-soft p-2 text-brand">
              {icon}
            </span>
          ) : null}
        </div>
      </div>
      {body}
      {drillHref ? (
        <Link to={drillHref} className="mt-2 self-start text-xs font-semibold text-brand hover:underline">
          <span className="nx-sr-only">{def.label}: </span>→
        </Link>
      ) : null}
    </section>
  );
  return card;
}
