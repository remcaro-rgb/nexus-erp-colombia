// Compras › Cotizaciones › Comparación (F-PUR-CYCLE): comparison matrix with normalized COP totals and the D-009 score
// breakdown, recommendation badge with its explanation, award dialog (reason required when it differs) and the award
// approval (SoD is enforced by the server; the UI only hides what the user may not do).
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { ApiError } from "@lib/api/client";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { Dialog } from "@ds/components/Dialog";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import type { ComparisonDto, PurchasingApi, RfqDto } from "../api";
import { awardErrors, CRITERIA, differsFromRecommendation, matrixRows, recommendationBadge, RFQ_STATUS_LABEL } from "../model";
import { usePurchasingApi } from "./usePurchasingApi";

export interface ComparisonViewProps {
  api: PurchasingApi;
  rfqId: string;
  canAward: boolean;
  canApprove: boolean;
}

export function ComparisonView({ api, rfqId, canAward, canApprove }: ComparisonViewProps) {
  const [rfq, setRfq] = useState<RfqDto | null>(null);
  const [comparison, setComparison] = useState<ComparisonDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [serverErrors, setServerErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [r, c] = await Promise.all([api.rfq(rfqId), api.comparison(rfqId)]);
      setRfq(r);
      setComparison(c);
      setChoice(c.recommended_quote_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No fue posible cargar el comparativo.");
    }
  }, [api, rfqId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <EmptyState title="Comparativo no disponible" body={error} action={<Button onClick={() => void load()}>Reintentar</Button>} />;
  if (!comparison || !rfq) return <Skeleton className="h-48 w-full" label="Cargando comparativo" />;

  const rows = matrixRows(comparison);
  const clientErrors = awardErrors(comparison, { quoteId: choice, reason });
  const submit = async () => {
    if (clientErrors.length) return;
    setBusy(true);
    setServerErrors([]);
    try {
      await api.award(rfqId, choice ?? "", reason);
      setOpen(false);
      await load();
    } catch (e) {
      setServerErrors([e instanceof ApiError ? `${e.code}: ${e.message}` : "Error al adjudicar."]);
    } finally {
      setBusy(false);
    }
  };
  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      await api.approveAward(rfqId, approve, approve ? "Aprobado" : reason || "Rechazado");
      await load();
    } catch (e) {
      setServerErrors([e instanceof ApiError ? `${e.code}: ${e.message}` : "Error en la aprobación."]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="nx-card flex flex-col gap-4" aria-labelledby="pur-cmp-title">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="pur-cmp-title" className="text-base font-bold text-fg">
            Comparativo {rfq.number} · versión {comparison.sequence}
          </h2>
          <p className="text-xs text-muted">Estado: {RFQ_STATUS_LABEL[rfq.status]}</p>
        </div>
        <Badge tone={comparison.recommended_quote_id ? "brand" : "neutral"}>{recommendationBadge(comparison)}</Badge>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" aria-label="Matriz de comparación">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th scope="col">#</th>
              <th scope="col">Proveedor</th>
              <th scope="col">Total puesto (COP)</th>
              {CRITERIA.map((c) => (
                <th scope="col" key={c.id}>
                  {c.label} ({comparison.weights[c.id]})
                </th>
              ))}
              <th scope="col">Puntaje</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.quoteId} data-testid={`row-${r.quoteId}`} className={r.excluded ? "text-muted" : undefined}>
                <td>{r.rank ?? "—"}</td>
                <td>
                  {r.vendor} {r.recommended ? <Badge tone="positive">Recomendada</Badge> : null}
                  {r.excluded ? <Badge tone="warning">{r.excluded}</Badge> : null}
                </td>
                <td className="text-right tabular-nums">{r.total}</td>
                {CRITERIA.map((c) => (
                  <td key={c.id} className="text-right tabular-nums">
                    {r.points ? r.points[c.id] : "—"}
                  </td>
                ))}
                <td className="text-right font-semibold tabular-nums">{r.score ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details>
        <summary className="cursor-pointer text-sm font-semibold">Explicación de la recomendación</summary>
        <pre className="whitespace-pre-wrap text-xs" data-testid="explanation">
          {comparison.explanation}
        </pre>
      </details>
      {serverErrors.length ? (
        <ul role="alert" className="text-sm text-danger">
          {serverErrors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <footer className="flex flex-wrap gap-2">
        {canAward && rfq.status === "EVALUATED" ? (
          <Button variant="primary" onClick={() => setOpen(true)}>
            Adjudicar
          </Button>
        ) : null}
        {canApprove && rfq.status === "AWARD_PENDING_APPROVAL" ? (
          <>
            <Button variant="primary" loading={busy} onClick={() => void decide(true)}>
              Aprobar adjudicación
            </Button>
            <Button variant="danger" loading={busy} onClick={() => void decide(false)}>
              Rechazar
            </Button>
          </>
        ) : null}
      </footer>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Adjudicar cotización"
        description="La adjudicación requiere aprobación de otra persona antes de crear la orden de compra."
        footer={
          <Button variant="primary" loading={busy} disabled={clientErrors.length > 0} onClick={() => void submit()}>
            Enviar a aprobación
          </Button>
        }
      >
        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-semibold">Cotización</legend>
          {comparison.ranked.map((q) => (
            <label key={q.quote_id} className="flex items-center gap-2 text-sm">
              <input type="radio" name="award-quote" value={q.quote_id} checked={choice === q.quote_id} onChange={() => setChoice(q.quote_id)} />
              {q.vendor_code} — {q.vendor_name} ({q.score} puntos)
            </label>
          ))}
        </fieldset>
        <label className="mt-3 flex flex-col gap-1 text-sm">
          Motivo {differsFromRecommendation(comparison, choice) ? "(obligatorio: difiere de la recomendación)" : "(opcional)"}
          <textarea value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} className="rounded border border-line p-2" />
        </label>
        {clientErrors.length ? (
          <ul aria-live="polite" className="mt-2 text-sm text-danger">
            {clientErrors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        ) : null}
      </Dialog>
    </section>
  );
}

export default function ComparisonPage() {
  const [params] = useSearchParams();
  const { api, can } = usePurchasingApi();
  const rfqId = params.get("rfq");
  if (!rfqId) return <EmptyState title="Seleccione una solicitud de cotización" body="Abra el comparativo desde la lista de solicitudes de cotización evaluadas." />;
  return <ComparisonView api={api} rfqId={rfqId} canAward={can("purchasing.rfq.approve")} canApprove={can("purchasing.rfq.approve")} />;
}
