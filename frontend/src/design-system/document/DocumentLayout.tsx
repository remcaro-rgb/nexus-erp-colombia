// Document pattern (owner P08) shared by invoice, order, PO, receipt, payroll run, asset, journal entry:
// header (title, number, state-machine badge) + lines grid + totals + side panel (related documents, accounting
// preview from `:simulate`, stock impact, fiscal status with the DIAN event log, attachments, audit timeline,
// comments) + a sticky action bar with EXACTLY ONE primary action.
import { Children, isValidElement, useId, useState, type ReactElement, type ReactNode } from "react";
import { cn } from "@lib/cn";
import { useT, type MessageKey } from "@i18n/index";
import { Tabs } from "../components/Tabs";
import { Button, type ButtonProps } from "../components/Button";

export type SidePanelId = "related" | "accounting" | "stock" | "fiscal" | "attachments" | "audit" | "comments";

const PANEL_LABEL: Record<SidePanelId, MessageKey> = {
  related: "doc.related",
  accounting: "doc.accounting",
  stock: "doc.stock",
  fiscal: "doc.fiscal",
  attachments: "doc.attachments",
  audit: "doc.audit",
  comments: "doc.comments",
};

export interface DocumentAction {
  id: string;
  label: string;
  onClick: () => void;
  primary?: boolean;
  disabled?: boolean;
  loading?: boolean;
  variant?: ButtonProps["variant"];
}

export interface DocumentLayoutProps {
  title: ReactNode;
  number?: ReactNode;
  state: ReactNode; // a PostingBadge / FiscalBadge
  header: ReactNode;
  lines: ReactNode;
  totals: ReactNode;
  panels: Partial<Record<SidePanelId, ReactNode>>;
  actions: DocumentAction[];
}

export function assertSinglePrimary(actions: DocumentAction[]): void {
  const n = actions.filter((a) => a.primary).length;
  if (n !== 1) throw new Error(`DocumentLayout needs exactly one primary action (got ${n})`);
}

export function DocumentLayout({ title, number, state, header, lines, totals, panels, actions }: DocumentLayoutProps) {
  const t = useT();
  const uid = useId().replace(/:/g, "");
  assertSinglePrimary(actions);
  const ids = (Object.keys(panels) as SidePanelId[]).filter((k) => panels[k] !== undefined);
  const [panel, setPanel] = useState<SidePanelId | undefined>(ids[0]);
  const ordered = [...actions.filter((a) => !a.primary), ...actions.filter((a) => a.primary)];
  return (
    <article className="flex flex-col gap-6" data-nx-component="document">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold text-fg">
            {title} {number ? <span className="font-mono text-base text-secondary">{number}</span> : null}
          </h1>
        </div>
        <div aria-label={t("doc.state")} role="group">
          {state}
        </div>
      </header>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_320px]">
        <div className="flex min-w-0 flex-col gap-6">
          <section className="nx-card p-6">{header}</section>
          <section className="nx-card p-6" aria-labelledby={`${uid}-lines`}>
            <h2 id={`${uid}-lines`} className="mb-3 text-base font-bold text-fg">
              {t("doc.lines")}
            </h2>
            {lines}
          </section>
          <section className="nx-card p-6" aria-labelledby={`${uid}-totals`}>
            <h2 id={`${uid}-totals`} className="mb-3 text-base font-bold text-fg">
              {t("doc.totals")}
            </h2>
            {totals}
          </section>
        </div>
        {ids.length && panel ? (
          <aside className="nx-card p-4" aria-label={t(PANEL_LABEL[panel])}>
            <Tabs tabs={ids.map((id) => ({ id, label: t(PANEL_LABEL[id]) }))} selected={panel} onSelect={(id) => setPanel(id as SidePanelId)} label={t("doc.actions")} idPrefix={`${uid}-side`}>
              {panels[panel]}
            </Tabs>
          </aside>
        ) : null}
      </div>
      <div role="toolbar" aria-label={t("doc.actions")} data-nx-print="hide" className="sticky bottom-0 z-[10] -mx-4 flex justify-end gap-2 border-t border-line bg-surface/95 px-4 py-3 md:-mx-6 md:px-6">
        {ordered.map((a) => (
          <Button key={a.id} variant={a.primary ? "primary" : (a.variant ?? "secondary")} onClick={a.onClick} disabled={a.disabled} loading={a.loading} data-primary={a.primary || undefined}>
            {a.label}
          </Button>
        ))}
      </div>
    </article>
  );
}

/** Utility for module code: count primary buttons in a rendered action list (used by tests and lint). */
export function countPrimary(children: ReactNode): number {
  let n = 0;
  Children.forEach(children, (c) => {
    if (isValidElement(c) && (c as ReactElement<{ variant?: string }>).props.variant === "primary") n++;
  });
  return n;
}

