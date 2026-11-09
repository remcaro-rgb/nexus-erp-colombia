/**
 * Contabilidad › Impuestos › Parámetros: one timeline per code with status, source and legal history,
 * UNKNOWN values as blocking items (fail closed), DISPUTED banner, "Cargar nuevo valor oficial" form
 * (source URL + uploaded document, two-person approval) and the impact preview.
 */

import { useEffect, useMemo, useState } from "react";

import { TaxApiError, taxApi, type TaxApiContext } from "./api";
import {
  STATUS_LABEL,
  blockingItems,
  buildTimelines,
  displayValue,
  needsLegalReview,
  type Parameter,
  type Timeline,
} from "./model";

interface Props {
  companyId: string;
  /** Reference date (business date of the selected period), ISO yyyy-mm-dd. */
  onDate: string;
  canPropose: boolean;
  canApprove: boolean;
  currentPrincipalId: string;
}

export function ParametersPage({ companyId, onDate, canPropose, canApprove, currentPrincipalId }: Props) {
  const [params, setParams] = useState<Parameter[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const ctx: TaxApiContext = { companyId, signal: controller.signal };
    taxApi
      .listParameters(ctx)
      .then((page) => setParams(page.items))
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setError(e instanceof TaxApiError ? `${e.code}: ${e.message}` : String(e));
      });
    return () => controller.abort(); // company switch: drop the old company's response
  }, [companyId, reload]);

  const timelines = useMemo(() => buildTimelines(params, onDate), [params, onDate]);
  const blocking = blockingItems(timelines);

  return (
    <section aria-labelledby="tax-params-title" className="flex flex-col gap-4">
      <h1 id="tax-params-title" className="text-lg font-semibold text-slate-900">
        Parámetros regulatorios
      </h1>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {blocking.length > 0 && (
        <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <strong>Bloqueado:</strong> estos parámetros no tienen un valor conocido el {onDate}; los cálculos que los usan
          fallan cerrados hasta cargar el valor oficial: {blocking.join(", ")}.
        </div>
      )}
      {timelines.map((t) => (
        <TimelineCard
          key={t.code}
          timeline={t}
          companyId={companyId}
          canApprove={canApprove}
          currentPrincipalId={currentPrincipalId}
          onChanged={() => setReload((n) => n + 1)}
        />
      ))}
      {canPropose && <ProposeForm companyId={companyId} onSaved={() => setReload((n) => n + 1)} />}
    </section>
  );
}

function TimelineCard({
  timeline,
  companyId,
  canApprove,
  currentPrincipalId,
  onChanged,
}: {
  timeline: Timeline;
  companyId: string;
  canApprove: boolean;
  currentPrincipalId: string;
  onChanged: () => void;
}) {
  const [impact, setImpact] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function approve(p: Parameter) {
    try {
      await taxApi.approveParameter({ companyId }, p.id, "Revisado contra la fuente oficial", true);
      setMessage("Valor aprobado.");
      onChanged();
    } catch (e) {
      setMessage(e instanceof TaxApiError ? `${e.code}: ${e.message}` : String(e));
    }
  }

  async function preview(p: Parameter) {
    const result = await taxApi.impact({ companyId }, p.id);
    setImpact(result.count === 0 ? "Ningún documento abierto cambia." : `${result.count} documentos abiertos cambiarían.`);
  }

  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4" aria-label={`Parámetro ${timeline.code}`}>
      <header className="flex items-center justify-between">
        <h2 className="font-mono text-sm font-semibold">{timeline.code}</h2>
        {timeline.disputed && (
          <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900">
            En disputa: el cálculo guarda una instantánea del parámetro
          </span>
        )}
      </header>
      <table className="mt-2 w-full text-left text-sm">
        <thead className="text-xs text-slate-500">
          <tr>
            <th scope="col">Vigencia</th>
            <th scope="col">Valor</th>
            <th scope="col">Estado</th>
            <th scope="col">Aprobación</th>
            <th scope="col">Historia legal</th>
            <th scope="col">
              <span className="sr-only">Acciones</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {timeline.entries.map(({ parameter: p, current }) => (
            <tr key={p.id} aria-current={current ? "true" : undefined} className={current ? "bg-indigo-50" : undefined}>
              <td>
                {p.valid_from} → {p.valid_to ?? "sin fin"}
              </td>
              <td>
                {displayValue(p)}
                {p.synthetic && <span className="ml-1 text-xs text-amber-800">FIXTURE — NO VERIFICADO LEGALMENTE</span>}
              </td>
              <td>{STATUS_LABEL[p.parameter_status]}</td>
              <td>
                {p.approval}
                {needsLegalReview(p) && <span className="ml-1 text-xs text-slate-600">(sin revisión legal EG-7)</span>}
              </td>
              <td>
                <ul className="text-xs">
                  {p.legal_history.map((e) => (
                    <li key={`${e.event}-${e.date}`}>
                      {e.date}: {e.event}
                      {e.source ? ` (${e.source})` : ""}
                    </li>
                  ))}
                </ul>
              </td>
              <td className="flex gap-2">
                <button type="button" className="text-xs underline" onClick={() => void preview(p)}>
                  Impacto
                </button>
                {canApprove && p.approval === "PROPOSED" && p.proposed_by !== currentPrincipalId && (
                  <button type="button" className="text-xs text-indigo-700 underline" onClick={() => void approve(p)}>
                    Aprobar
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {impact && <p className="mt-2 text-xs text-slate-700">{impact}</p>}
      {message && (
        <p role="status" className="mt-2 text-xs">
          {message}
        </p>
      )}
    </article>
  );
}

function ProposeForm({ companyId, onSaved }: { companyId: string; onSaved: () => void }) {
  const [message, setMessage] = useState<string | null>(null);

  async function submit(form: HTMLFormElement) {
    const f = new FormData(form);
    const parameters = {
      code: f.get("code"),
      value_type: f.get("value_type"),
      value: f.get("value"),
      valid_from: f.get("valid_from"),
      valid_to: f.get("valid_to") || null,
      parameter_status: f.get("parameter_status"),
      source: { url: f.get("source_url"), title: f.get("source_title"), accessed_at: new Date().toISOString() },
      document_ref: f.get("document_ref"),
    };
    try {
      await taxApi.proposeParameter({ companyId }, parameters, String(f.get("reason") ?? ""));
      setMessage("Propuesta registrada. Otra persona debe aprobarla antes de que se use.");
      onSaved();
    } catch (e) {
      setMessage(e instanceof TaxApiError ? `${e.code}: ${e.message}` : String(e));
    }
  }

  return (
    <form
      className="grid grid-cols-2 gap-2 rounded-xl border border-slate-200 bg-white p-4 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        void submit(e.currentTarget);
      }}
    >
      <h2 className="col-span-2 font-semibold">Cargar nuevo valor oficial</h2>
      <label>
        Código <input name="code" required maxLength={40} />
      </label>
      <label>
        Tipo
        <select name="value_type" required>
          <option value="decimal">Valor</option>
          <option value="percent">Porcentaje</option>
          <option value="permil">Por mil</option>
          <option value="uvt">UVT</option>
        </select>
      </label>
      <label>
        Valor (texto decimal) <input name="value" required inputMode="decimal" pattern="-?[0-9]+(\.[0-9]+)?" />
      </label>
      <label>
        Estado
        <select name="parameter_status" required>
          <option value="VERIFIED-PRIMARY">Verificado (fuente oficial)</option>
          <option value="VERIFIED-SECONDARY">Verificado (fuente secundaria)</option>
          <option value="DISPUTED">En disputa</option>
        </select>
      </label>
      <label>
        Vigente desde <input name="valid_from" type="date" required />
      </label>
      <label>
        Vigente hasta <input name="valid_to" type="date" />
      </label>
      <label className="col-span-2">
        URL de la fuente oficial <input name="source_url" type="url" required className="w-full" />
      </label>
      <label className="col-span-2">
        Título de la fuente <input name="source_title" required maxLength={255} className="w-full" />
      </label>
      <label className="col-span-2">
        Documento cargado (referencia del archivo) <input name="document_ref" required className="w-full" />
      </label>
      <label className="col-span-2">
        Motivo <textarea name="reason" required maxLength={2000} className="w-full" />
      </label>
      <button type="submit" className="col-span-2 rounded-lg bg-indigo-600 px-3 py-2 text-white">
        Proponer valor (requiere aprobación de otra persona)
      </button>
      {message && (
        <p role="status" className="col-span-2">
          {message}
        </p>
      )}
    </form>
  );
}
