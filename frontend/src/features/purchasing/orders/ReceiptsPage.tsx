// Compras › Órdenes y recepciones › Recepciones: receipts with their order and the validation action.
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@lib/api/client";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { formatCOP } from "@lib/money";
import type { ReceiptDto } from "../api";
import { usePurchasingApi } from "../rfq/usePurchasingApi";

export default function ReceiptsPage() {
  const { api, companyId, can } = usePurchasingApi();
  const [rows, setRows] = useState<ReceiptDto[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const load = useCallback(() => {
    api.receipts().then(
      (p) => setRows(p.items),
      () => setRows([]),
    );
  }, [api]);
  useEffect(() => {
    setRows(null);
    load();
  }, [load, companyId]);
  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!rows.length) return <EmptyState title="Aún no hay recepciones" />;
  const validate = async (id: string) => {
    try {
      await api.validateReceipt(id);
      setMessage("Recepción validada: inventario y contabilidad actualizados.");
      load();
    } catch (e) {
      setMessage(e instanceof ApiError ? `${e.code}: ${e.message}` : "Error al validar.");
    }
  };
  return (
    <div className="flex flex-col gap-2">
      {message ? <p role="status" className="text-sm">{message}</p> : null}
      <table className="nx-card w-full text-sm" aria-label="Recepciones">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th scope="col">Número</th>
            <th scope="col">Orden</th>
            <th scope="col">Estado</th>
            <th scope="col">Valor (COP)</th>
            <th scope="col">Acción</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.number}</td>
              <td>{r.order.number}</td>
              <td>
                <Badge tone={r.receipt_status === "VALIDATED" ? "positive" : "neutral"}>{r.receipt_status === "VALIDATED" ? "Validada" : "Borrador"}</Badge>
              </td>
              <td className="text-right tabular-nums">{formatCOP(r.total.amount)}</td>
              <td>
                {r.receipt_status === "DRAFT" && can("purchasing.receipt.update") ? (
                  <Button onClick={() => void validate(r.id)}>Validar</Button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
