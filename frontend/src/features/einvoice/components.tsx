/** Presentational pieces of Facturación electrónica (props only; containers live in pages.tsx). */
import { useState, type FormEvent } from "react";
import { Badge } from "@ds/components/Badge";
import {
  FAMILY_LABEL,
  PROVIDER_BADGE,
  DELIVERABLE,
  RETRYABLE,
  contingencyMessage,
  failedCases,
  isSecretRef,
  rangeSize,
  statusText,
  statusTone,
  type ContingencyPanel,
  type ContractReport,
  type FiscalDocument,
  type NumberingRange,
  type TechnologyProvider,
} from "./model";

export function ContingencyBanner({ panel, canAdmin, onDeclare, onEnd }: {
  panel: ContingencyPanel | null | undefined;
  canAdmin: boolean;
  onDeclare?: (reason: string) => void;
  onEnd?: () => void;
}) {
  const [reason, setReason] = useState("");
  const msg = contingencyMessage(panel);
  return (
    <div className="flex flex-col gap-2">
      {msg && (
        <div role="status" aria-live="polite" className={`rounded-xl border p-3 text-sm ${msg.tone === "danger" ? "border-red-200 bg-red-50 text-red-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
          {msg.text}
        </div>
      )}
      {canAdmin && panel && !panel.active && onDeclare && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (reason.trim()) onDeclare(reason.trim());
          }}
        >
          <label className="flex flex-col text-sm">
            Motivo de la contingencia
            <input className="nx-input" value={reason} onChange={(e) => setReason(e.target.value)} required maxLength={500} />
          </label>
          <button type="submit" className="nx-btn">Declarar contingencia</button>
        </form>
      )}
      {canAdmin && panel?.active && onEnd && (
        <button type="button" className="nx-btn self-start" onClick={onEnd}>
          Servicio restablecido — terminar contingencia
        </button>
      )}
    </div>
  );
}

export function DocumentsTable({ docs, canUpdate, canExport, onRetry, onResend, xmlHref, caption }: {
  docs: FiscalDocument[];
  canUpdate: boolean;
  canExport: boolean;
  onRetry: (id: string) => void;
  onResend: (id: string) => void;
  xmlHref: (id: string) => string;
  caption: string;
}) {
  if (docs.length === 0) {
    return (
      <div className="nx-card p-4 text-sm">
        <p>No hay documentos electrónicos en el periodo. Se generan al contabilizar facturas en Ventas o POS.</p>
        <a className="nx-link" href="/m/ventas/facturas">Ir a Facturas de venta</a>
      </div>
    );
  }
  return (
    <table className="nx-table w-full text-sm">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Número</th>
          <th scope="col">Tipo</th>
          <th scope="col">Estado</th>
          <th scope="col">CUFE/CUDE</th>
          <th scope="col"><span className="sr-only">Acciones</span></th>
        </tr>
      </thead>
      <tbody>
        {docs.map((d) => (
          <tr key={d.id}>
            <td>{d.source.number ?? "—"}</td>
            <td>{FAMILY_LABEL[d.document_type] ?? d.document_type}</td>
            <td>
              <Badge tone={statusTone(d.status)}>{statusText(d)}</Badge>
            </td>
            <td className="font-mono" title={d.cufe ?? undefined}>{d.cufe ? `${d.cufe.slice(0, 12)}…` : "—"}</td>
            <td className="flex gap-2">
              {canUpdate && RETRYABLE.has(d.status) && (
                <button type="button" className="nx-btn" onClick={() => onRetry(d.id)}>Reintentar</button>
              )}
              {canUpdate && DELIVERABLE.has(d.status) && (
                <button type="button" className="nx-btn" onClick={() => onResend(d.id)}>Reenviar correo</button>
              )}
              {canExport && (
                <a className="nx-link" href={xmlHref(d.id)} download>XML</a>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RangesTable({ ranges }: { ranges: NumberingRange[] }) {
  if (ranges.length === 0) return <p className="text-sm">No hay resoluciones de numeración cargadas.</p>;
  return (
    <table className="nx-table w-full text-sm">
      <caption className="sr-only">Rangos de numeración autorizados</caption>
      <thead>
        <tr>
          <th scope="col">Código</th>
          <th scope="col">Prefijo</th>
          <th scope="col">Desde</th>
          <th scope="col">Hasta</th>
          <th scope="col">Tamaño</th>
          <th scope="col">Resolución</th>
          <th scope="col">Vigente hasta</th>
        </tr>
      </thead>
      <tbody>
        {ranges.map((r) => (
          <tr key={r.id}>
            <td>{r.code}</td>
            <td>{r.prefix}</td>
            <td>{r.from_number.toLocaleString("es-CO")}</td>
            <td>{r.to_number.toLocaleString("es-CO")}</td>
            <td>{rangeSize(r).toLocaleString("es-CO")}</td>
            <td>{r.resolution}</td>
            <td>{r.valid_to}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export interface ProviderDraft {
  code: string;
  name: string;
  adapter: string;
  base_url: string;
  credentials_ref: string;
}

export function ProvidersPanel({ providers, canCreate, canAdmin, canExport, exportHref, reports, onCreate, onTest }: {
  providers: TechnologyProvider[];
  canCreate: boolean;
  canAdmin: boolean;
  canExport: boolean;
  exportHref: string;
  reports: Record<string, ContractReport | undefined>;
  onCreate: (draft: ProviderDraft) => void;
  onTest: (id: string) => void;
}) {
  const [draft, setDraft] = useState<ProviderDraft>({ code: "", name: "", adapter: "http_generic", base_url: "https://", credentials_ref: "" });
  const [refError, setRefError] = useState<string | null>(null);
  const set = (k: keyof ProviderDraft) => (e: { target: { value: string } }) => setDraft({ ...draft, [k]: e.target.value });
  return (
    <section aria-labelledby="pt-title" className="flex flex-col gap-3">
      <h2 id="pt-title" className="text-base font-semibold">Proveedores tecnológicos</h2>
      {canExport && (
        <a className="nx-link self-start" href={exportHref} download>Exportar configuración (sin secretos)</a>
      )}
      {providers.length === 0 ? (
        <p className="text-sm">No hay proveedores tecnológicos configurados: se usa el simulador.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {providers.map((p) => {
            const report = reports[p.id];
            return (
              <li key={p.id} className="nx-card flex flex-col gap-1 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <strong>{p.name}</strong> <span className="text-xs">({p.code} · {p.adapter} · {p.environment})</span>
                  <Badge tone="warning">{PROVIDER_BADGE}</Badge>
                </div>
                <span className="text-xs">{p.base_url}</span>
                {canAdmin && (
                  <button type="button" className="nx-btn self-start" onClick={() => onTest(p.id)}>Probar contrato</button>
                )}
                {report && (
                  <p role="status" className="text-sm">
                    {report.passed ? "Prueba de contrato superada" : `Prueba de contrato fallida: ${failedCases(report).join(", ")}`}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {canCreate && (
        <form
          aria-label="Nuevo proveedor tecnológico"
          className="grid gap-2 sm:grid-cols-2"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (draft.credentials_ref && !isSecretRef(draft.credentials_ref)) {
              setRefError("Use una referencia secret://einvoice/<nombre>; las credenciales nunca se escriben aquí.");
              return;
            }
            setRefError(null);
            onCreate(draft);
          }}
        >
          <label className="flex flex-col text-sm">Código<input className="nx-input" value={draft.code} onChange={set("code")} required maxLength={40} /></label>
          <label className="flex flex-col text-sm">Nombre<input className="nx-input" value={draft.name} onChange={set("name")} required maxLength={200} /></label>
          <label className="flex flex-col text-sm">Adaptador
            <select className="nx-input" value={draft.adapter} onChange={set("adapter")}>
              <option value="http_generic">Conector HTTP genérico</option>
              <option value="simulator">Simulador</option>
            </select>
          </label>
          <label className="flex flex-col text-sm">URL base (sandbox)<input className="nx-input" value={draft.base_url} onChange={set("base_url")} required /></label>
          <label className="flex flex-col text-sm sm:col-span-2">Referencia de credenciales
            <input className="nx-input font-mono" value={draft.credentials_ref} onChange={set("credentials_ref")} placeholder="secret://einvoice/pt-credenciales" autoComplete="off" aria-describedby="pt-ref-help" />
            <span id="pt-ref-help" className="text-xs">Las credenciales se guardan en el archivo de secretos del operador; aquí solo va la referencia.</span>
          </label>
          {refError && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{refError}</p>}
          <button type="submit" className="nx-btn sm:col-span-2 justify-self-start">Registrar proveedor</button>
        </form>
      )}
    </section>
  );
}
