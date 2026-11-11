// "Exportar" (/m/datos/exportar), owner P18: template entities to CSV/XLSX/JSONL with the source module's row and
// field policy applied (columns the user may not read are omitted); every export is audited with its row count.
import { useState } from "react";
import { ApiError } from "@lib/api/client";
import { Button } from "@ds/components/Button";
import { EmptyState } from "@ds/components/Feedback";
import { Field, Select } from "@ds/components/Field";
import { useToast } from "@ds/components/Toast";
import { useResource, useShell } from "@shell/ShellContext";
import type { PageComponent } from "@shell/registry";
import { dataioApi, downloadPayload, problemText, type ExportJob, type Page, type Template } from "./api";
import { msg } from "./messages";

const ExportPage: PageComponent = () => {
  const shell = useShell();
  const toast = useToast();
  const list = useResource<Page<ExportJob>>("/api/v1/dataio/exports", { company_id: shell.companyId });
  const templates = useResource<Page<Template>>("/api/v1/dataio/templates", { company_id: shell.companyId, limit: 500 });
  const [source, setSource] = useState("");
  const [format, setFormat] = useState("xlsx");
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

  const items = list.data?.items ?? [];
  return (
    <div className="flex flex-col gap-6" data-nx-page="datos.exportar">
      <section aria-label={msg.newExport} className="grid max-w-3xl grid-cols-1 items-end gap-4 sm:grid-cols-3">
        <Field label={msg.template} required>
          {(p) => (
            <Select id={p.id} value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">{msg.choose}</option>
              {(templates.data?.items ?? []).map((t) => (
                <option key={t.entity} value={t.entity}>{t.label}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label={msg.format}>
          {(p) => (
            <Select id={p.id} value={format} onChange={(e) => setFormat(e.target.value)}>
              <option value="xlsx">XLSX</option>
              <option value="csv">CSV</option>
              <option value="jsonl">JSONL</option>
            </Select>
          )}
        </Field>
        <Button
          variant="primary"
          loading={busy}
          disabled={!source}
          onClick={() =>
            guarded(async () => {
              const job = await client.createExport(source, format);
              downloadPayload(await client.exportFile(job.id));
              toast.push({ tone: "success", title: msg.exported });
              list.reload();
            })
          }
        >
          {msg.newExport}
        </Button>
      </section>
      {!list.loading && !items.length ? (
        <EmptyState title={msg.emptyExports} body={msg.emptyExportsBody} />
      ) : (
        <table className="w-full text-sm" aria-label={msg.exportsTitle}>
          <thead>
            <tr className="text-left text-secondary">
              <th className="py-2">{msg.template}</th>
              <th>{msg.format}</th>
              <th className="text-right">{msg.rows}</th>
              <th>{msg.omitted}</th>
              <th>{msg.date}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((j) => (
              <tr key={j.id} className="border-t border-line">
                <td className="py-2">{j.source}</td>
                <td>{j.format.toUpperCase()}</td>
                <td className="text-right tabular-nums">{j.row_count.toLocaleString("es-CO")}</td>
                <td>{j.omitted_columns.join(", ")}</td>
                <td>{j.audit?.created_at ? new Date(j.audit.created_at).toLocaleString("es-CO", { timeZone: "America/Bogota" }) : ""}</td>
                <td>
                  <Button variant="ghost" onClick={() => guarded(async () => downloadPayload(await client.exportFile(j.id)))}>{msg.download}</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

export default ExportPage;
