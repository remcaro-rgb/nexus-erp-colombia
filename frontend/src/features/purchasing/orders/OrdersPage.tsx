// Compras › Órdenes y recepciones › Órdenes de compra: status, origin RFQ and receiving / invoicing progress.
import { useEffect, useState } from "react";
import { Badge } from "@ds/components/Badge";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { formatCOP } from "@lib/money";
import type { OrderDto } from "../api";
import { ORDER_STATUS_LABEL, progress } from "./model";
import { usePurchasingApi } from "../rfq/usePurchasingApi";

export default function OrdersPage() {
  const { api, companyId } = usePurchasingApi();
  const [rows, setRows] = useState<OrderDto[] | null>(null);
  useEffect(() => {
    let live = true;
    setRows(null);
    api.orders().then(
      (p) => live && setRows(p.items),
      () => live && setRows([]),
    );
    return () => {
      live = false;
    };
  }, [api, companyId]);
  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!rows.length) return <EmptyState title="Aún no hay órdenes de compra" body="Las órdenes nacen de una adjudicación aprobada o se crean manualmente." />;
  return (
    <table className="nx-card w-full text-sm" aria-label="Órdenes de compra">
      <thead>
        <tr className="text-left text-xs text-muted">
          <th scope="col">Número</th>
          <th scope="col">Proveedor</th>
          <th scope="col">Estado</th>
          <th scope="col">Origen</th>
          <th scope="col">Total</th>
          <th scope="col">Recibido / facturado</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((o) => (
          <tr key={o.id}>
            <td>{o.number}</td>
            <td>{o.party?.name ?? "—"}</td>
            <td>
              <Badge>{ORDER_STATUS_LABEL[o.status] ?? o.status}</Badge>
            </td>
            <td>{o.rfq?.number ?? "Manual"}</td>
            <td className="text-right tabular-nums">{o.currency === "COP" ? formatCOP(o.total.amount) : `${o.total.amount} ${o.currency}`}</td>
            <td>{progress(o)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
