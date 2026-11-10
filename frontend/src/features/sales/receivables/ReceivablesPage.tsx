// Ventas › Cartera: AR aging by customer (COP) and the overdue share. AR balances are confidential: without the field
// grant the server answers 403 field_forbidden and the page says so (fail closed, same check as API/exports/AI/MCP).
import { useEffect, useState } from "react";
import { ApiError } from "@lib/api/client";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { formatCOP } from "@lib/money";
import type { AgingDto } from "../api";
import { AGING_LABEL, overdueShare } from "../model";
import { useSalesApi } from "../useSalesApi";

export default function ReceivablesPage() {
  const { api, companyId } = useSalesApi();
  const [aging, setAging] = useState<AgingDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setAging(null);
    setError(null);
    api.aging().then(
      (a) => live && setAging(a),
      (e: unknown) => live && setError(e instanceof ApiError && e.status === 403 ? "No tiene permiso para ver saldos de cartera." : "No fue posible cargar la cartera."),
    );
    return () => {
      live = false;
    };
  }, [api, companyId]);
  if (error) return <EmptyState title="Cartera" body={error} />;
  if (!aging) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!aging.parties.length) return <EmptyState title="Sin cartera pendiente" body={`Corte ${aging.as_of}.`} />;
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm">
        Corte {aging.as_of} · total {formatCOP(aging.total)} · vencida {overdueShare(aging)}
      </p>
      <table className="nx-card w-full text-sm" aria-label="Edades de cartera">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th scope="col">Cliente</th>
            {aging.buckets.map((b) => (
              <th key={b} scope="col">
                {AGING_LABEL[b]}
              </th>
            ))}
            <th scope="col">Total</th>
          </tr>
        </thead>
        <tbody>
          {aging.parties.map((row) => (
            <tr key={row.party.id}>
              <td>{row.name}</td>
              {aging.buckets.map((b) => (
                <td key={b} className="text-right tabular-nums">
                  {formatCOP(row.buckets[b] ?? "0")}
                </td>
              ))}
              <td className="text-right tabular-nums">{formatCOP(row.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
