// Tesorería › Egresos › Lotes de pago (F-TRE-CE): submit, approve (SoD / dual), execute → one numbered CE per payee
// and a SIMULATED bank file labelled FIXTURE_NOT_CERTIFIED (no live bank).
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { formatCOP } from "@lib/money";
import type { BatchDto, OperationDto } from "../api";
import { STATUS_LABEL, STATUS_TONE } from "../model";
import { useTreasuryApi } from "../useTreasuryApi";

export default function BatchesPage() {
  const { api, companyId, can } = useTreasuryApi();
  const [rows, setRows] = useState<BatchDto[] | null>(null);
  const [file, setFile] = useState<OperationDto["file"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    let live = true;
    setRows(null);
    api.batches().then(
      (p) => live && setRows(p.items),
      () => live && setRows([]),
    );
    return () => {
      live = false;
    };
  }, [api]);
  useEffect(() => reload(), [reload, companyId]);
  const act = (b: BatchDto, action: "submit" | "approve" | "execute") => {
    setError(null);
    api.batchAction(b.id, action).then(
      (res) => {
        if ("file" in res && res.file) setFile(res.file);
        reload();
      },
      (e: Error) => setError(e.message),
    );
  };
  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!rows.length) return <EmptyState title="Sin lotes de pago" body="Los lotes agrupan pagos a proveedores; al ejecutarse generan un egreso por beneficiario." />;
  return (
    <section aria-label="Lotes de pago" className="space-y-3">
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
      <table className="nx-card w-full text-sm" aria-label="Lotes de pago">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th scope="col">Número</th>
            <th scope="col">Fecha de pago</th>
            <th scope="col">Estado</th>
            <th scope="col">Total</th>
            <th scope="col">Acciones</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => (
            <tr key={b.id}>
              <td>{b.number}</td>
              <td>{b.payment_date}</td>
              <td>
                <Badge tone={STATUS_TONE[b.status]}>{b.status === "POSTED" ? "Ejecutado" : STATUS_LABEL[b.status]}</Badge>
              </td>
              <td className="text-right tabular-nums">{formatCOP(b.total.amount)}</td>
              <td className="flex gap-1">
                {(b.status === "DRAFT" || b.status === "REJECTED") && can("treasury.payment_batch.update") && (
                  <Button type="button" variant="secondary" onClick={() => act(b, "submit")}>
                    Enviar a aprobación
                  </Button>
                )}
                {b.status === "SUBMITTED" && can("treasury.payment_batch.approve") && (
                  <Button type="button" variant="secondary" onClick={() => act(b, "approve")}>
                    Aprobar
                  </Button>
                )}
                {b.status === "APPROVED" && can("treasury.payment_batch.post") && (
                  <Button type="button" onClick={() => act(b, "execute")}>
                    Ejecutar pagos
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {file && (
        <figure className="nx-card">
          <figcaption>
            Archivo bancario <Badge tone="warning">{file.label}</Badge> — simulado, no certificado (SHA-256 {file.sha256.slice(0, 12)}…)
          </figcaption>
          <pre className="text-xs">{file.content}</pre>
        </figure>
      )}
    </section>
  );
}
