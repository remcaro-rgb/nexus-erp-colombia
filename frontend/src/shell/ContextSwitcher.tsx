// Company + period selectors (owner P08, UI-CONTRACT §4). Companies = only the permitted ones from /ui/context.
// Switching re-scopes every query (ShellContext aborts in-flight requests and clears the cache first).
// Below 1024 px both merge into one context button ("EMP · nov 2026") that opens a dialog.
import { useState } from "react";
import { Building2, CalendarDays } from "lucide-react";
import { useT } from "@i18n/index";
import { Dialog } from "@ds/components/Dialog";
import { Field, Select } from "@ds/components/Field";
import { periodLabel } from "@lib/dates";
import type { CompanyRef, PeriodRef } from "@lib/api/types";
import { useShell } from "./ShellContext";

function companiesOf(ctx: ReturnType<typeof useShell>["context"]): CompanyRef[] {
  if (ctx.companies?.length) return ctx.companies;
  return (ctx.company_ids ?? []).map((id) => ({ id, legal_name: id }));
}

function periodsOf(ctx: ReturnType<typeof useShell>["context"], current: PeriodRef | null): PeriodRef[] {
  const list = ctx.periods?.length ? ctx.periods : current ? [current] : [];
  return current && !list.some((p) => p.id === current.id) ? [current, ...list] : list;
}

export function CompanySelect({ id }: { id?: string }) {
  const t = useT();
  const shell = useShell();
  const companies = companiesOf(shell.context);
  const current = companies.find((c) => c.id === shell.companyId);
  return (
    <Select
      id={id}
      aria-label={id ? undefined : t("header.company")}
      title={current ? `${current.legal_name}${current.nit ? ` — ${t("company.nit", { nit: current.nit })}` : ""}` : undefined}
      className="max-w-[240px] truncate"
      value={shell.companyId ?? ""}
      onChange={(e) => shell.setCompany(e.target.value)}
      data-nx-control="company"
    >
      {!shell.companyId ? <option value="">{t("company.none")}</option> : null}
      {companies.map((c) => (
        <option key={c.id} value={c.id} title={c.nit ? t("company.nit", { nit: c.nit }) : undefined}>
          {c.legal_name}
        </option>
      ))}
    </Select>
  );
}

export function PeriodSelect({ id }: { id?: string }) {
  const t = useT();
  const shell = useShell();
  const periods = periodsOf(shell.context, shell.period);
  const statusText = (p: PeriodRef) => (p.status === "open" ? t("period.open") : t("period.closed"));
  return (
    <Select
      id={id}
      aria-label={id ? undefined : t("header.period")}
      className="max-w-[160px]"
      value={shell.period?.id ?? ""}
      onChange={(e) => {
        const p = periods.find((x) => x.id === e.target.value);
        if (p) shell.setPeriod(p);
      }}
      data-nx-control="period"
    >
      {!shell.period ? <option value="">{t("period.none")}</option> : null}
      {periods.map((p) => (
        <option key={p.id} value={p.id}>
          {`${p.label ?? periodLabel(p.start)}${p.status === "open" ? "" : " 🔒"} · ${statusText(p)}`}
        </option>
      ))}
    </Select>
  );
}

export function ContextButton() {
  const t = useT();
  const shell = useShell();
  const [open, setOpen] = useState(false);
  const company = companiesOf(shell.context).find((c) => c.id === shell.companyId);
  const short = (company?.code ?? company?.legal_name ?? "").slice(0, 3).toUpperCase();
  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t("header.context")}
        onClick={() => setOpen(true)}
        className="inline-flex min-h-8 items-center gap-1 rounded-control border border-line px-2 text-xs font-semibold text-body hover:bg-nav-hover"
      >
        <Building2 aria-hidden="true" size={14} />
        <span>
          {short || t("company.none")} · {shell.period ? (shell.period.label ?? periodLabel(shell.period.start)) : t("period.none")}
        </span>
        <CalendarDays aria-hidden="true" size={14} />
      </button>
      <Dialog open={open} onOpenChange={setOpen} title={t("header.context")}>
        <div className="flex flex-col gap-4">
          <Field label={t("header.company")} id="nx-ctx-company">
            {() => <CompanySelect id="nx-ctx-company" />}
          </Field>
          <Field label={t("header.period")} id="nx-ctx-period">
            {() => <PeriodSelect id="nx-ctx-period" />}
          </Field>
        </div>
      </Dialog>
    </>
  );
}
