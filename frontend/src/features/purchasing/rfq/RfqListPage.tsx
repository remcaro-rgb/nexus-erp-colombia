// Compras › Cotizaciones › Solicitudes de cotización: list with status, deadline and the next step.
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Badge } from "@ds/components/Badge";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { formatDateTimeBogota } from "../format";
import type { RfqDto } from "../api";
import { RFQ_STATUS_LABEL } from "../model";
import { usePurchasingApi } from "./usePurchasingApi";

export default function RfqListPage() {
  const { api, companyId } = usePurchasingApi();
  const [rows, setRows] = useState<RfqDto[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let live = true;
    setRows(null);
    api.rfqs().then(
      (p) => live && setRows(p.items),
      () => live && setError(true),
    );
    return () => {
      live = false; // a response for a previous company is discarded
    };
  }, [api, companyId]);
  if (error) return <EmptyState title="No fue posible cargar las solicitudes de cotización" />;
  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!rows.length) return <EmptyState title="Aún no hay solicitudes de cotización" body="Cree una solicitud e invite al menos un proveedor (se recomiendan 3)." />;
  return (
    <table className="nx-card w-full text-sm" aria-label="Solicitudes de cotización">
      <thead>
        <tr className="text-left text-xs text-muted">
          <th scope="col">Número</th>
          <th scope="col">Estado</th>
          <th scope="col">Cierre</th>
          <th scope="col">Proveedores</th>
          <th scope="col">Comparativo</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td>{r.number}</td>
            <td>
              <Badge>{RFQ_STATUS_LABEL[r.status]}</Badge>
            </td>
            <td>{formatDateTimeBogota(r.deadline)}</td>
            <td>{r.invited_vendors.length}</td>
            <td>{["EVALUATED", "AWARD_PENDING_APPROVAL", "AWARDED", "PO_CREATED"].includes(r.status) ? <Link to={`/m/compras/cotizaciones/comparacion?rfq=${r.id}`}>Ver comparativo</Link> : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
