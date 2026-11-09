// EmptyState, Skeleton, Stepper, Timeline, Spinner-free loading (owner P08).
import type { ReactNode } from "react";
import { cn } from "@lib/cn";
import { useT } from "@i18n/index";

export function EmptyState({ title, body, action, icon }: { title: ReactNode; body?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      {icon ? <div aria-hidden="true" className="text-muted">{icon}</div> : null}
      <p className="text-sm font-semibold text-fg">{title}</p>
      {body ? <p className="max-w-md text-sm text-muted">{body}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function Skeleton({ className, label }: { className?: string; label?: string }) {
  return (
    <div
      role={label ? "status" : undefined}
      aria-label={label}
      className={cn("animate-pulse rounded-control bg-nav-hover motion-reduce:animate-none", className)}
    />
  );
}

export interface Step {
  id: string;
  label: string;
}

export function Stepper({ steps, current }: { steps: Step[]; current: number }) {
  const t = useT();
  return (
    <nav aria-label={t("ds.stepper")}>
      <ol className="flex flex-wrap items-center gap-2">
        {steps.map((s, i) => {
          const state = i < current ? "done" : i === current ? "current" : "todo";
          return (
            <li key={s.id} className="flex items-center gap-2" aria-current={state === "current" ? "step" : undefined}>
              <span
                className={cn(
                  "inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold",
                  state === "done" && "bg-positive-bg text-positive",
                  state === "current" && "bg-brand text-white dark:text-slate-950",
                  state === "todo" && "bg-nav-hover text-secondary",
                )}
              >
                {i + 1}
              </span>
              <span className={cn("text-sm", state === "current" ? "font-semibold text-fg" : "text-secondary")}>
                <span className="nx-sr-only">{t("ds.stepper.step", { n: i + 1, total: steps.length, label: "" })}</span>
                {s.label}
              </span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export interface TimelineItem {
  id: string;
  title: ReactNode;
  at: string;
  body?: ReactNode;
}

export function Timeline({ items, label }: { items: TimelineItem[]; label?: string }) {
  const t = useT();
  return (
    <ol aria-label={label ?? t("ds.timeline")} className="relative ml-2 border-l border-line">
      {items.map((it) => (
        <li key={it.id} className="mb-4 ml-4">
          <span aria-hidden="true" className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border border-surface bg-brand" />
          <p className="text-sm font-medium text-fg">{it.title}</p>
          <p className="text-xs text-muted">{it.at}</p>
          {it.body ? <div className="mt-1 text-sm text-body">{it.body}</div> : null}
        </li>
      ))}
    </ol>
  );
}
