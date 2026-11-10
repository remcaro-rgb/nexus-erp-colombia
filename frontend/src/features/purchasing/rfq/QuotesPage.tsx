// Compras › Cotizaciones › Cotizaciones recibidas: quotes of one RFQ (?rfq=<id>) with status (LATE quotes flagged).
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { Badge } from "@ds/components/Badge";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { formatCOP } from "@lib/money";
import type { QuoteDto } from "../api";
import { formatDateTimeBogota } from "../format";
import { QUOTE_STATUS_LABEL } from "../model";
import { usePurchasingApi } from "./usePurchasingApi";

export default function QuotesPage() {
  const [params] = useSearchParams();
  const rfqId = params.get("rfq");
  const { api, companyId } = usePurchasingApi();
  const [rows, setRows] = useState<QuoteDto[] | null>(null);
  useEffect(() => {
    if (!rfqId) return;
    let live = true;
    api.quotes(rfqId).then(
      (p) => live && setRows(p.items),
      () => live && setRows([]),
    );
    return () => {
      live = false;
    };
  }, [api, rfqId, companyId]);
  if (!rfqId) return <EmptyState title="Seleccione una solicitud de cotización" />;
  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!rows.length) return <EmptyState title="Sin cotizaciones recibidas" />;
  return (
    <table className="nx-card w-full text-sm" aria-label="Cotizaciones recibidas">
      <thead>
        <tr className="text-left text-xs text-muted">
          <th scope="col">Número</th>
          <th scope="col">Proveedor</th>
          <th scope="col">Recibida</th>
          <th scope="col">Estado</th>
          <th scope="col">Total puesto (COP)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((q) => (
          <tr key={q.id}>
            <td>{q.number}</td>
            <td>{q.party?.name ?? "—"}</td>
            <td>{formatDateTimeBogota(q.received_at)}</td>
            <td>
              <Badge tone={q.quote_status === "LATE" ? "warning" : "neutral"}>{QUOTE_STATUS_LABEL[q.quote_status]}</Badge>
            </td>
            <td className="text-right tabular-nums">{q.landed_total ? formatCOP(q.landed_total.amount) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
