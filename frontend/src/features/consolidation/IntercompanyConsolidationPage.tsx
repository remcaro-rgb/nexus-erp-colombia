// "Intercompañía y consolidación" (/m/tesoreria/intercompania), owner P16b — contributed to the Tesorería module
// through the shell's contribution API (module.ts). Four keyboard-accessible tabs (WAI-ARIA tablist):
// IC reconciliation, IC agreements, consolidation groups/runs (preview → post with SoD on the server) and the
// consolidated statements (balance, income statement, eliminations) with CSV export. Every action uses the public
// API; success messages appear only after the API confirms.
import { useMemo, useState, type ReactNode } from "react";
import { ApiError } from "@lib/api/client";
import { DataGrid, type GridColumn } from "@ds/data-grid";
import { Button } from "@ds/components/Button";
import { Dialog } from "@ds/components/Dialog";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { Field, Input, Select } from "@ds/components/Field";
import { Tabs } from "@ds/components/Tabs";
import { useToast } from "@ds/components/Toast";
import { useResource, useShell } from "@shell/ShellContext";
import type { PageComponent } from "@shell/registry";
import {
  BASE,
  IC_BASE,
  consolidationApi,
  exportUrl,
  periodEnd,
  problemText,
  reportRows,
  validateAgreement,
  validateRun,
  type ConsolidationGroup,
  type ConsolidationRun,
  type GridRow,
  type IntercompanyAgreement,
  type Page,
  type ReportResult,
  type SimulationResult,
} from "./api";
import { msg } from "./messages";

type TabId = "reconciliation" | "agreements" | "runs" | "statements";
const TAB_IDS: TabId[] = ["reconciliation", "agreements", "runs", "statements"];

function useCompanyName(): (id: string) => string {
  const shell = useShell();
  const names = useMemo(() => new Map((shell.context.companies ?? []).map((c) => [c.id, c.code ? `${c.code} — ${c.legal_name}` : c.legal_name])), [shell.context.companies]);
  return (id: string) => names.get(id) ?? id;
}

function useErrorToast() {
  const toast = useToast();
  return (e: unknown, setErrors?: (errs: Record<string, string>) => void) => {
    if (e instanceof ApiError) {
      setErrors?.(e.fieldErrors());
      toast.push({ tone: "error", title: problemText(e.code, e.message), traceId: e.traceId });
    } else throw e;
  };
}

function Loadable<T>({ res, empty, children }: { res: { data: T | null; error: unknown; loading: boolean; reload: () => void }; empty?: boolean; children: (data: T) => ReactNode }) {
  if (res.loading && !res.data) return <Skeleton className="h-40" label={msg.loading} />;
  if (res.error) {
    const code = res.error instanceof ApiError ? res.error.code : undefined;
    return <EmptyState title={problemText(code, msg.loadError)} action={<Button onClick={res.reload}>{msg.retry}</Button>} />;
  }
  if (!res.data || empty) return null;
  return <>{children(res.data)}</>;
}

function ReportGrid({ label, resource, report }: { label: string; resource: string; report: ReportResult }) {
  const columns: GridColumn<GridRow>[] = report.columns.map((c) => ({ id: c.key, header: c.label, type: c.type === "money" ? "decimal" : c.type === "integer" ? "count" : "text" }));
  return (
    <div className="flex flex-col gap-2">
      <DataGrid label={label} resource={resource} rows={reportRows(report)} columns={columns} height={360} />
      {report.totals ? (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
          {Object.entries(report.totals).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-2">
              <dt className="text-[var(--nx-color-text-muted)]">{k}</dt>
              <dd className="font-mono tabular-nums">{String(v)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {(report.warnings ?? []).map((w, i) => (
        <p key={i} role="alert" className="text-sm text-[var(--nx-color-warning-fg,inherit)]">{w.message}</p>
      ))}
    </div>
  );
}

function ReconciliationTab() {
  const [period, setPeriod] = useState("");
  const query = /^\d{4}-(0[1-9]|1[0-2])$/.test(period) ? period : undefined;
  const res = useResource<ReportResult>(`${IC_BASE}/reconciliation`, { period: query });
  const name = useCompanyName();
  return (
    <section aria-label={msg.tabs.reconciliation} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <Field label={msg.period} hint={msg.periodInvalid}>{(p) => <Input id={p.id} aria-describedby={p.describedBy} value={period} onChange={(e) => setPeriod(e.target.value)} placeholder="2026-11" />}</Field>
        <Button onClick={res.reload}>{msg.refresh}</Button>
        <a className="text-sm underline" href={exportUrl(`${IC_BASE}/reconciliation`, { period: query })}>{msg.exportCsv}</a>
      </div>
      <Loadable res={res}>
        {(rep) =>
          rep.rows.length === 0 ? (
            <EmptyState title={msg.noReconciliation} />
          ) : (
            <ReportGrid
              label={msg.tabs.reconciliation}
              resource="intercompany.reconciliation"
              report={{ ...rep, rows: rep.rows.map((r) => ({ ...r, company_a_id: name(String(r.company_a_id)), company_b_id: name(String(r.company_b_id)), status: r.status === "MIRRORED" ? msg.mirrored : msg.mismatch })) }}
            />
          )
        }
      </Loadable>
    </section>
  );
}

function AgreementsTab() {
  const shell = useShell();
  const toast = useToast();
  const onError = useErrorToast();
  const name = useCompanyName();
  const res = useResource<Page<IntercompanyAgreement>>(`${IC_BASE}/agreements`, { limit: 500 });
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ code: "", name: "", company_a_id: "", company_b_id: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const companies = shell.context.companies ?? [];
  async function save() {
    const errs = validateAgreement(form, { required: msg.required, uuid: msg.uuid, same: msg.same });
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const created = await consolidationApi(shell.api).createAgreement({ ...form, code: form.code.trim(), name: form.name.trim(), accounts: {} });
      toast.push({ tone: "success", title: msg.created(created.code) });
      setCreating(false);
      res.reload();
    } catch (e) {
      onError(e, setErrors);
    } finally {
      setBusy(false);
    }
  }
  const columns: GridColumn<GridRow>[] = [
    { id: "code", header: msg.code },
    { id: "name", header: msg.name },
    { id: "company_a", header: msg.companyA },
    { id: "company_b", header: msg.companyB },
    { id: "status", header: msg.status },
  ];
  const companySelect = (k: "company_a_id" | "company_b_id", label: string) => (
    <Field label={label} error={errors[k]} required>
      {(p) => (
        <Select id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid || undefined} value={form[k]} onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}>
          <option value="">{msg.choose}</option>
          {companies.map((c) => (
            <option key={c.id} value={c.id}>{name(c.id)}</option>
          ))}
        </Select>
      )}
    </Field>
  );
  return (
    <section aria-label={msg.tabs.agreements} className="flex flex-col gap-3">
      <div><Button variant="primary" onClick={() => setCreating((v) => !v)}>{msg.newAgreement}</Button></div>
      {creating ? (
        <div className="grid max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={msg.code} error={errors.code} required>{(p) => <Input id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid || undefined} value={form.code} maxLength={40} onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))} />}</Field>
          <Field label={msg.name} error={errors.name} required>{(p) => <Input id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid || undefined} value={form.name} maxLength={200} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />}</Field>
          {companySelect("company_a_id", msg.companyA)}
          {companySelect("company_b_id", msg.companyB)}
          <div className="flex gap-2 sm:col-span-2">
            <Button variant="primary" loading={busy} onClick={() => void save()}>{msg.save}</Button>
            <Button variant="ghost" onClick={() => setCreating(false)}>{msg.cancel}</Button>
          </div>
        </div>
      ) : null}
      <Loadable res={res}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title={msg.noAgreements} />
          ) : (
            <DataGrid
              label={msg.tabs.agreements}
              resource="intercompany.agreement"
              columns={columns}
              rows={page.items.map((a) => ({ id: a.id, code: a.code, name: a.name, company_a: name(a.company_a_id), company_b: name(a.company_b_id), status: msg.statusLabel[a.status] ?? a.status }))}
              height={320}
            />
          )
        }
      </Loadable>
    </section>
  );
}

function RunsTab() {
  const shell = useShell();
  const toast = useToast();
  const onError = useErrorToast();
  const groups = useResource<Page<ConsolidationGroup>>(`${BASE}/groups`, { limit: 500 });
  const runs = useResource<Page<ConsolidationRun>>(`${BASE}/runs`, { limit: 500 });
  const [form, setForm] = useState({ group_id: "", period: "", document_date: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<{ run: ConsolidationRun; sim: SimulationResult } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const client = consolidationApi(shell.api);
  async function createRun() {
    const data = { ...form, document_date: form.document_date || periodEnd(form.period) };
    const errs = validateRun(data, { required: msg.required, period: msg.periodInvalid });
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy("create");
    try {
      const run = await client.createRun(data);
      toast.push({ tone: "success", title: msg.created(run.number) });
      runs.reload();
    } catch (e) {
      onError(e, setErrors);
    } finally {
      setBusy(null);
    }
  }
  async function doPreview(run: ConsolidationRun) {
    setBusy(`preview:${run.id}`);
    try {
      setPreview({ run, sim: await client.preview(run.id) });
    } catch (e) {
      onError(e);
    } finally {
      setBusy(null);
    }
  }
  const [confirming, setConfirming] = useState<ConsolidationRun | null>(null);
  async function doPost(run: ConsolidationRun) {
    setConfirming(null);
    setBusy(`post:${run.id}`);
    try {
      const posted = await client.post(run.id);
      toast.push({ tone: "success", title: msg.posted(posted.number) });
      setPreview(null);
      runs.reload();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(null);
    }
  }
  const groupName = new Map((groups.data?.items ?? []).map((g) => [g.id, `${g.code} — ${g.name}`]));
  return (
    <section aria-label={msg.tabs.runs} className="flex flex-col gap-4">
      <Loadable res={groups}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title={msg.noGroups} />
          ) : (
            <div className="grid max-w-3xl grid-cols-1 items-end gap-3 sm:grid-cols-4">
              <Field label={msg.group} error={errors.group_id} required>
                {(p) => (
                  <Select id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid || undefined} value={form.group_id} onChange={(e) => setForm((f) => ({ ...f, group_id: e.target.value }))}>
                    <option value="">{msg.choose}</option>
                    {page.items.filter((g) => g.status === "ACTIVE").map((g) => (
                      <option key={g.id} value={g.id}>{`${g.code} — ${g.name} (${g.presentation_currency})`}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label={msg.period} error={errors.period} required>{(p) => <Input id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid || undefined} placeholder="2026-11" value={form.period} onChange={(e) => setForm((f) => ({ ...f, period: e.target.value }))} />}</Field>
              <Field label={msg.documentDate} error={errors.document_date}>{(p) => <Input id={p.id} type="date" value={form.document_date} onChange={(e) => setForm((f) => ({ ...f, document_date: e.target.value }))} />}</Field>
              <Button variant="primary" loading={busy === "create"} onClick={() => void createRun()}>{msg.newRun}</Button>
            </div>
          )
        }
      </Loadable>
      <Loadable res={runs}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title={msg.noRuns} />
          ) : (
            <table className="w-full text-sm">
              <caption className="sr-only">{msg.tabs.runs}</caption>
              <thead>
                <tr className="text-left">
                  <th scope="col">{msg.number}</th>
                  <th scope="col">{msg.group}</th>
                  <th scope="col">{msg.period}</th>
                  <th scope="col">{msg.status}</th>
                  <th scope="col" className="text-right">{msg.total}</th>
                  <th scope="col"><span className="sr-only">Acciones</span></th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((r) => (
                  <tr key={r.id} className="border-t border-[var(--nx-color-border)]">
                    <td className="py-1 font-mono">{r.number}</td>
                    <td>{groupName.get(r.group_id) ?? r.group_id}</td>
                    <td>{r.period}</td>
                    <td>{msg.statusLabel[r.status] ?? r.status}</td>
                    <td className="text-right font-mono tabular-nums">{`${r.total.amount} ${r.total.currency}`}</td>
                    <td className="flex justify-end gap-2 py-1">
                      {r.status === "DRAFT" ? (
                        <>
                          <Button loading={busy === `preview:${r.id}`} onClick={() => void doPreview(r)}>{msg.preview}</Button>
                          <Button variant="primary" loading={busy === `post:${r.id}`} onClick={() => setConfirming(r)}>{msg.post}</Button>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Loadable>
      <Dialog
        open={confirming !== null}
        onOpenChange={(open) => { if (!open) setConfirming(null); }}
        title={msg.post}
        description={confirming ? msg.postConfirm(confirming.number) : ""}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirming(null)}>{msg.cancel}</Button>
            <Button variant="primary" onClick={() => (confirming ? void doPost(confirming) : undefined)}>{msg.post}</Button>
          </div>
        }
      >
        <p className="text-sm">{msg.synthetic}</p>
      </Dialog>
      {preview ? (
        <section aria-label={msg.previewTitle} className="flex flex-col gap-2">
          <h3 className="font-semibold">{`${msg.previewTitle} — ${preview.run.number}`}</h3>
          {(preview.sim.warnings ?? []).map((w, i) => <p key={i} role="alert" className="text-sm">{w.message}</p>)}
          {preview.sim.journal_entries.map((e, i) => (
            <table key={i} className="w-full text-sm">
              <caption className="text-left font-medium">{`${e.journal.replace("CONSOLIDACION/", "")} · ${e.memo ?? ""}`}</caption>
              <tbody>
                {e.lines.map((ln, j) => (
                  <tr key={j}>
                    <td className="font-mono">{ln.account}</td>
                    <td className="text-right font-mono tabular-nums">{ln.debit.amount}</td>
                    <td className="text-right font-mono tabular-nums">{ln.credit.amount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        </section>
      ) : null}
    </section>
  );
}

function StatementsTab() {
  const runs = useResource<Page<ConsolidationRun>>(`${BASE}/runs`, { limit: 500 });
  const [runId, setRunId] = useState("");
  const params = { run_id: runId || undefined };
  const balance = useResource<ReportResult>(`${BASE}/reports/balance`, params);
  const income = useResource<ReportResult>(`${BASE}/reports/income-statement`, params);
  const elim = useResource<ReportResult>(`${BASE}/reports/eliminations`, params);
  const choices = (runs.data?.items ?? []).filter((r) => r.status !== "CANCELLED");
  const block = (title: string, path: string, resource: string, res: typeof balance) => (
    <section aria-label={title} className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{title}</h3>
        <a className="text-sm underline" href={exportUrl(`${BASE}/reports/${path}`, params)}>{msg.exportCsv}</a>
      </div>
      <Loadable res={res}>{(rep) => (rep.rows.length === 0 ? <EmptyState title={msg.noPosted} /> : <ReportGrid label={title} resource={resource} report={rep} />)}</Loadable>
    </section>
  );
  return (
    <section aria-label={msg.tabs.statements} className="flex flex-col gap-4">
      <Field label={msg.run}>
        {(p) => (
          <Select id={p.id} value={runId} onChange={(e) => setRunId(e.target.value)}>
            <option value="">{msg.latestPosted}</option>
            {choices.map((r) => (
              <option key={r.id} value={r.id}>{`${r.number} (${msg.statusLabel[r.status] ?? r.status})`}</option>
            ))}
          </Select>
        )}
      </Field>
      {block(msg.balance, "balance", "consolidation.balance", balance)}
      {block(msg.income, "income-statement", "consolidation.income_statement", income)}
      {block(msg.eliminations, "eliminations", "consolidation.eliminations", elim)}
    </section>
  );
}

const IntercompanyConsolidationPage: PageComponent = () => {
  const [tab, setTab] = useState<TabId>("reconciliation");
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold">{msg.title}</h2>
        <p className="text-sm text-[var(--nx-color-text-muted)]">{msg.intro}</p>
        <p className="text-xs text-[var(--nx-color-text-muted)]">{msg.synthetic}</p>
      </header>
      <Tabs label={msg.title} idPrefix="ic-cons" tabs={TAB_IDS.map((id) => ({ id, label: msg.tabs[id] }))} selected={tab} onSelect={(id) => setTab(id as TabId)}>
        {tab === "reconciliation" ? <ReconciliationTab /> : null}
        {tab === "agreements" ? <AgreementsTab /> : null}
        {tab === "runs" ? <RunsTab /> : null}
        {tab === "statements" ? <StatementsTab /> : null}
      </Tabs>
    </div>
  );
};

export default IntercompanyConsolidationPage;
