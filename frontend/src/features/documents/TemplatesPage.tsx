// Reportes › Documentos (P17a, D-005): company document templates — list, edit the sandboxed markup with a client
// pre-check, preview against sample data (server-rendered, never persisted; sandboxed frame without scripts),
// versioned on save (If-Match). Built-in templates apply when the company has none for a code.
import { useEffect, useState } from "react";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { createApiClient, ApiError } from "@lib/api/client";
import { can } from "@lib/permissions";
import { useOptionalShell } from "@shell/ShellContext";
import { DocumentsApi, type PreviewDto, type TemplateDto } from "./api";
import { etagFor, precheck, previewDocument } from "./model";

export default function TemplatesPage() {
  const shell = useOptionalShell();
  const companyId = shell?.companyId ?? null;
  const [api] = useState(() => new DocumentsApi(shell?.api ?? createApiClient(), companyId));
  const [rows, setRows] = useState<TemplateDto[] | null>(null);
  const [selected, setSelected] = useState<TemplateDto | null>(null);
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState<PreviewDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const writable = shell ? can(shell.perms, "documents.template.update") : false;

  useEffect(() => {
    let live = true;
    api.templates().then(
      (p) => live && setRows(p.items),
      () => live && setRows([]),
    );
    return () => {
      live = false;
    };
  }, [api]);

  const open = (t: TemplateDto) => {
    setSelected(t);
    setBody(t.body);
    setPreview(null);
    setError(null);
  };
  const doPreview = async () => {
    if (!selected) return;
    try {
      setPreview(await api.preview(selected.id));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.code}: ${e.message}` : "No se pudo previsualizar");
    }
  };
  const save = async () => {
    if (!selected) return;
    const problem = precheck(body);
    if (problem) return setError(problem);
    try {
      const etag = await etagFor("documents.DocumentTemplate", selected.id, selected.version);
      const t = await api.update(selected, body, etag);
      setSelected(t);
      setRows((rs) => rs?.map((r) => (r.id === t.id ? t : r)) ?? null);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.code}: ${e.message}` : "No se pudo guardar");
    }
  };

  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando plantillas" />;
  return (
    <section aria-label="Plantillas de documentos" className="grid gap-4 md:grid-cols-2">
      <div>
        {!rows.length ? (
          <EmptyState title="Sin plantillas propias" body="Se usan las plantillas incorporadas (recibos de caja, egresos, etiquetas, facturas, certificados…)." />
        ) : (
          <ul className="nx-card space-y-1" aria-label="Plantillas">
            {rows.map((t) => (
              <li key={t.id}>
                <button type="button" className="w-full text-left" onClick={() => open(t)} aria-current={selected?.id === t.id}>
                  {t.code} — {t.name} <Badge tone={t.status === "ACTIVE" ? "positive" : "neutral"}>v{t.version}</Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {selected ? (
        <div className="space-y-2">
          <label className="flex flex-col text-sm">
            Plantilla {selected.code} (marcado seguro)
            <textarea rows={16} value={body} onChange={(e) => setBody(e.target.value)} readOnly={!writable} spellCheck={false} />
          </label>
          <div className="flex gap-2">
            <Button onClick={doPreview}>Previsualizar</Button>
            {writable ? (
              <Button variant="primary" onClick={save}>
                Guardar versión
              </Button>
            ) : null}
          </div>
          {error ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : null}
          {preview?.documents[0] ? (
            <iframe
              title={`Vista previa de ${selected.code}`}
              sandbox=""
              className="h-96 w-full border"
              srcDoc={previewDocument(preview.documents[0].html)}
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
