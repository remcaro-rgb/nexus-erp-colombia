// "Depreciación" (/m/activos/depreciacion), owner P14: runs per book and period, :preview (journal entry that
// would be posted, nothing persisted) and :post (one summarized entry per book; posting twice keeps one entry).
import { useState } from "react";
import { ApiError } from "@lib/api/client";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { Field, Select } from "@ds/components/Field";
import { useToast } from "@ds/components/Toast";
import { formatUnit } from "@ds/components/format";
import { useResource, useShell } from "@shell/ShellContext";
import type { PageComponent } from "@shell/registry";
import { assetsApi, problemText, type AssetBook, type DepreciationRun, type Page, type Simulation } from "./api";
import { msg } from "./messages";

const DepreciationPage: PageComponent = () => {
  const shell = useShell();
  const toast = useToast();
  const books = useResource<Page<AssetBook>>("/api/v1/assets/books", { company_id: shell.companyId });
  const runs = useResource<Page<DepreciationRun>>("/api/v1/assets/depreciation-runs", { company_id: shell.companyId, sort: "-document_date" });
  const [bookId, setBookId] = useState("");
  const [preview, setPreview] = useState<{ run: DepreciationRun; sim: Simulation } | null>(null);
  const [busy, setBusy] = useState(false);
  const client = assetsApi(shell.api, shell.companyId);
  const period = shell.period;
  const selectedBook = bookId || books.data?.items[0]?.id || "";

  async function guarded(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      if (e instanceof ApiError) toast.push({ tone: "error", title: problemText(e.code, e.message), traceId: e.traceId });
      else throw e;
    } finally {
      setBusy(false);
    }
  }

  const create = () =>
    guarded(async () => {
      if (!period) return;
      const run = await client.createRun({ document_date: period.end, period_id: period.id, book_id: selectedBook });
      toast.push({ tone: "success", title: msg.runCreated(run.number) });
      setPreview({ run, sim: await client.previewRun(run.id) });
      runs.reload();
    });
  const showPreview = (run: DepreciationRun) => guarded(async () => setPreview({ run, sim: await client.previewRun(run.id) }));
  const post = (run: DepreciationRun) =>
    guarded(async () => {
      const posted = await client.postRun(run.id);
      toast.push({ tone: "success", title: msg.runPosted(posted.number, posted.total.amount) });
      setPreview(null);
      runs.reload();
    });

  const bookCode = (id: string) => books.data?.items.find((b) => b.id === id)?.code ?? "";
  return (
    <div className="flex flex-col gap-4" data-nx-page="activos.depreciacion">
      <div className="flex flex-wrap items-end gap-3">
        <Field label={msg.book}>
          {(p) => (
            <Select id={p.id} value={selectedBook} onChange={(e) => setBookId(e.target.value)}>
              {(books.data?.items ?? []).map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </Select>
          )}
        </Field>
        <span className="text-sm">{`${msg.period}: ${period?.label ?? period?.start?.slice(0, 7) ?? "—"}`}</span>
        <Button variant="primary" loading={busy} disabled={!period || !selectedBook} onClick={() => void create()}>{msg.newRun}</Button>
      </div>
      {!period ? <p className="text-sm">{msg.noPeriod}</p> : null}
      {runs.loading ? <Skeleton className="h-40 w-full" label={msg.loading} /> : null}
      {runs.error ? <EmptyState title={msg.loadError} action={<Button onClick={runs.reload}>{msg.retry}</Button>} /> : null}
      {!runs.loading && !runs.error && !(runs.data?.items.length ?? 0) ? <EmptyState title={msg.runsEmpty} /> : null}
      {runs.data?.items.length ? (
        <table className="w-full text-sm" aria-label={msg.depreciationTitle}>
          <thead>
            <tr className="text-left">
              <th>{msg.number}</th>
              <th>{msg.book}</th>
              <th>{msg.date}</th>
              <th>{msg.status}</th>
              <th className="text-right">{msg.total}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.data.items.map((r) => (
              <tr key={r.id}>
                <td>{r.number}</td>
                <td>{bookCode(r.book_id)}</td>
                <td>{r.document_date}</td>
                <td>{msg.docStatus[r.status] ?? r.status}</td>
                <td className="text-right tabular-nums">{formatUnit("COP", r.total.amount)}</td>
                <td className="flex gap-2">
                  {r.status === "DRAFT" ? (
                    <>
                      <Button variant="ghost" onClick={() => void showPreview(r)}>{msg.preview}</Button>
                      <Button variant="primary" loading={busy} onClick={() => void post(r)}>{msg.post}</Button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {preview ? (
        <section aria-label={msg.preview} className="rounded border border-[var(--nx-color-border)] p-4">
          <h2 className="mb-2 font-semibold">{`${msg.preview} — ${preview.run.number}`}</h2>
          {(preview.sim.explain ?? []).map((line) => (
            <p key={line} className="text-sm">{line}</p>
          ))}
          {preview.sim.journal_entries.map((je) => (
            <table key={je.posting_date} className="mt-2 w-full text-sm">
              <thead>
                <tr className="text-left">
                  <th>{msg.account}</th>
                  <th className="text-right">{msg.debit}</th>
                  <th className="text-right">{msg.credit}</th>
                </tr>
              </thead>
              <tbody>
                {je.lines.map((ln, i) => (
                  <tr key={`${ln.account}-${i}`}>
                    <td>{ln.account}</td>
                    <td className="text-right tabular-nums">{formatUnit("COP", ln.debit.amount)}</td>
                    <td className="text-right tabular-nums">{formatUnit("COP", ln.credit.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        </section>
      ) : null}
    </div>
  );
};

export default DepreciationPage;
