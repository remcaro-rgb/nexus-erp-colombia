// "Plantillas" (/m/datos/plantillas), owner P18 (F-X-DATAIO UI): every template with its field help, natural key
// and a downloadable example file (CSV/XLSX).
import { ApiError } from "@lib/api/client";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { useToast } from "@ds/components/Toast";
import { useResource, useShell } from "@shell/ShellContext";
import type { PageComponent } from "@shell/registry";
import { dataioApi, downloadPayload, problemText, type Page, type Template } from "./api";
import { msg } from "./messages";

const TemplatesPage: PageComponent = () => {
  const shell = useShell();
  const toast = useToast();
  const res = useResource<Page<Template>>("/api/v1/dataio/templates", { company_id: shell.companyId, limit: 500 });
  const client = dataioApi(shell.api, shell.companyId);

  async function download(entity: string, fmt: string) {
    try {
      downloadPayload(await client.templateFile(entity, fmt));
    } catch (e) {
      if (e instanceof ApiError) toast.push({ tone: "error", title: problemText(e.code, e.message), traceId: e.traceId });
      else throw e;
    }
  }

  if (res.loading) return <Skeleton label={msg.templatesTitle} className="h-40" />;
  const items = res.data?.items ?? [];
  if (!items.length) return <EmptyState title={msg.templatesTitle} body={msg.emptyImportsBody} />;
  return (
    <div className="flex flex-col gap-6" data-nx-page="datos.plantillas">
      {items.map((t) => (
        <section key={t.entity} aria-labelledby={`tpl-${t.entity}`} className="rounded-[12px] border border-line p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 id={`tpl-${t.entity}`} className="font-semibold">
              {t.label} <code className="text-xs text-secondary">{t.entity}</code>
            </h3>
            <div className="flex gap-2">
              {t.document ? <Badge tone="warning">BORRADOR</Badge> : null}
              <Button variant="ghost" onClick={() => void download(t.entity, "xlsx")}>{msg.example} XLSX</Button>
              <Button variant="ghost" onClick={() => void download(t.entity, "csv")}>CSV</Button>
            </div>
          </div>
          <p className="mt-1 text-sm text-secondary">
            {msg.naturalKey}: {t.natural_key.join(" + ")}
            {t.hierarchy ? ` · ${msg.hierarchy}` : ""}
            {t.notes ? ` · ${t.notes}` : ""}
          </p>
          <table className="mt-2 w-full text-sm" aria-label={`${msg.templatesTitle} ${t.label}`}>
            <thead>
              <tr className="text-left text-secondary">
                <th className="py-1">{msg.column}</th>
                <th>{msg.type}</th>
                <th>{msg.required}</th>
                <th>{msg.help}</th>
              </tr>
            </thead>
            <tbody>
              {t.columns.map((c) => (
                <tr key={c.name} className="border-t border-line">
                  <td className="py-1"><code>{c.name}</code></td>
                  <td>{c.type}</td>
                  <td>{c.required ? msg.yes : msg.no}</td>
                  <td>
                    {c.help}
                    {c.allowed?.length ? ` (${c.allowed.join(", ")})` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
};

export default TemplatesPage;
