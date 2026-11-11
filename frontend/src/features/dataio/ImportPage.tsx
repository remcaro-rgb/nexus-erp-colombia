// "Importar" (/m/datos/importar, quick action /m/datos/importar/nueva), owner P18: choose a template, load the
// file (validated by a dry-run that executes the real services and rolls back), review row errors, then confirm.
// A file with any row error applies zero rows. Success is shown only after the API answers.
import { useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { ApiError } from "@lib/api/client";
import { Badge, type BadgeTone } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState } from "@ds/components/Feedback";
import { Field, Input, Select } from "@ds/components/Field";
import { useToast } from "@ds/components/Toast";
import { useResource, useShell } from "@shell/ShellContext";
import type { PageComponent } from "@shell/registry";
import { dataioApi, downloadPayload, problemText, toBase64, type ImportJob, type Page, type Template } from "./api";
import { msg } from "./messages";

const TONE: Record<string, BadgeTone> = { COMMITTED: "positive", FAILED: "danger", VALIDATED: "brand", CANCELLED: "neutral" };

export function statusBadge(status: string) {
  return <Badge tone={TONE[status] ?? "warning"}>{msg.statuses[status] ?? status}</Badge>;
}

const ImportPage: PageComponent = () => {
  const shell = useShell();
  const toast = useToast();
  const location = useLocation();
  const [search] = useSearchParams();
  const estado = search.get("estado") ?? "";
  const tipo = search.get("tipo") ?? "";
  const list = useResource<Page<ImportJob>>("/api/v1/dataio/imports", { company_id: shell.companyId, estado, tipo });
  const templates = useResource<Page<Template>>("/api/v1/dataio/templates", { company_id: shell.companyId, limit: 500 });
  const [open, setOpen] = useState(location.pathname.endsWith("/nueva"));
  const [entity, setEntity] = useState(tipo);
  const [mode, setMode] = useState("upsert");
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [busy, setBusy] = useState(false);
  const client = dataioApi(shell.api, shell.companyId);

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

  const loadAndValidate = () =>
    guarded(async () => {
      if (!file || !entity) return;
      const created = await client.createImport(entity, mode);
      await client.upload(created.id, file.name, toBase64(await file.arrayBuffer()));
      const validated = await client.validate(created.id);
      setJob(validated);
      if (validated.import_status === "VALIDATED") toast.push({ tone: "success", title: msg.validated });
      list.reload();
    });

  const confirm = () =>
    guarded(async () => {
      if (!job) return;
      const op = await client.commit(job.id);
      setJob(await client.importJob(op.import.id));
      toast.push({ tone: "success", title: msg.ok });
      list.reload();
    });

  const items = list.data?.items ?? [];
  return (
    <div className="flex flex-col gap-6" data-nx-page="datos.importar">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{msg.importsTitle}</h2>
        <Button variant="primary" onClick={() => setOpen((v) => !v)}>{msg.newImport}</Button>
      </div>
      {open ? (
        <section aria-label={msg.newImport} className="grid max-w-3xl grid-cols-1 gap-4 rounded-[12px] border border-line p-4 sm:grid-cols-2">
          <Field label={msg.template} required>
            {(p) => (
              <Select id={p.id} value={entity} onChange={(e) => setEntity(e.target.value)}>
                <option value="">{msg.choose}</option>
                {(templates.data?.items ?? []).map((t) => (
                  <option key={t.entity} value={t.entity}>{t.label}</option>
                ))}
              </Select>
            )}
          </Field>
          <Field label={msg.mode}>
            {(p) => (
              <Select id={p.id} value={mode} onChange={(e) => setMode(e.target.value)}>
                {Object.entries(msg.modes).map(([k, label]) => (
                  <option key={k} value={k}>{label}</option>
                ))}
              </Select>
            )}
          </Field>
          <Field label={msg.file} required hint={msg.atomic}>
            {(p) => <Input id={p.id} type="file" accept=".csv,.xlsx,.jsonl,.zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}
          </Field>
          <p className="text-sm text-secondary sm:col-span-2">{msg.draftNote}</p>
          <div className="flex gap-2 sm:col-span-2">
            <Button variant="primary" loading={busy} disabled={!file || !entity} onClick={loadAndValidate}>{msg.upload}</Button>
            {job?.import_status === "VALIDATED" ? <Button loading={busy} onClick={confirm}>{msg.commit}</Button> : null}
            {job ? (
              <Button variant="ghost" onClick={() => guarded(async () => downloadPayload(await client.report(job.id, "xlsx")))}>{msg.report}</Button>
            ) : null}
          </div>
          {job ? <JobDetail job={job} /> : null}
        </section>
      ) : null}
      {!list.loading && !items.length ? (
        <EmptyState title={msg.emptyImports} body={msg.emptyImportsBody} />
      ) : (
        <table className="w-full text-sm" aria-label={msg.importsTitle}>
          <thead>
            <tr className="text-left text-secondary">
              <th className="py-2">{msg.template}</th>
              <th>{msg.status}</th>
              <th className="text-right">{msg.rows}</th>
              <th className="text-right">{msg.applied}</th>
              <th className="text-right">{msg.errors}</th>
              <th>{msg.date}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((j) => (
              <tr key={j.id} className="border-t border-line">
                <td className="py-2">{j.entity}</td>
                <td>{statusBadge(j.import_status)}</td>
                <td className="text-right tabular-nums">{j.row_count.toLocaleString("es-CO")}</td>
                <td className="text-right tabular-nums">{j.applied_rows.toLocaleString("es-CO")}</td>
                <td className="text-right tabular-nums">{j.error_count.toLocaleString("es-CO")}</td>
                <td>{j.audit?.created_at ? new Date(j.audit.created_at).toLocaleString("es-CO", { timeZone: "America/Bogota" }) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

export function JobDetail({ job }: { job: ImportJob }) {
  const counts = job.counts ?? {};
  return (
    <div className="flex flex-col gap-2 sm:col-span-2" data-nx-import-status={job.import_status}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        {statusBadge(job.import_status)}
        <span>{msg.created}: {counts.created ?? 0}</span>
        <span>{msg.updated}: {counts.updated ?? 0}</span>
        <span>{msg.unchanged}: {counts.unchanged ?? 0}</span>
      </div>
      {job.errors?.length ? (
        <table className="w-full text-sm" aria-label={msg.errors}>
          <thead>
            <tr className="text-left text-secondary">
              <th className="py-1">Fila</th>
              <th>{msg.column}</th>
              <th>Código</th>
              <th>Mensaje</th>
            </tr>
          </thead>
          <tbody>
            {job.errors.slice(0, 200).map((e, i) => (
              <tr key={`${e.row}-${i}`} className="border-t border-line">
                <td className="py-1 tabular-nums">{e.row}</td>
                <td>{e.column ?? ""}</td>
                <td><code>{e.code}</code></td>
                <td>{e.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

export default ImportPage;
