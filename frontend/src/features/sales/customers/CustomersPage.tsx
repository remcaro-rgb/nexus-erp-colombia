// Ventas › Clientes y precios › Clientes: customer parties (NIT with DV, group, terms, credit limit). Confidential fields
// (NIT, credit limit) arrive only when the user's field policy allows them; the page shows "—" otherwise.
import { useEffect, useState } from "react";
import { Badge } from "@ds/components/Badge";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import type { PartyDto } from "../api";
import { useSalesApi } from "../useSalesApi";
import { amountText } from "../orders/DocumentsTable";

export default function CustomersPage() {
  const { api, companyId } = useSalesApi();
  const [rows, setRows] = useState<PartyDto[] | null>(null);
  useEffect(() => {
    let live = true;
    setRows(null);
    api.parties({ status: "ACTIVE" }).then(
      (p) => live && setRows(p.items.filter((x) => x.roles.includes("customer"))),
      () => live && setRows([]),
    );
    return () => {
      live = false;
    };
  }, [api, companyId]);
  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!rows.length) return <EmptyState title="Aún no hay clientes" body="Cree clientes o conviértalos desde un prospecto con su autorización de datos." />;
  return (
    <table className="nx-card w-full text-sm" aria-label="Clientes">
      <thead>
        <tr className="text-left text-xs text-muted">
          <th scope="col">Código</th>
          <th scope="col">Razón social</th>
          <th scope="col">NIT</th>
          <th scope="col">Grupo</th>
          <th scope="col">Plazo</th>
          <th scope="col">Cupo</th>
          <th scope="col">Estado</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
          <tr key={p.id}>
            <td>{p.code}</td>
            <td>{p.legal_name ?? "—"}</td>
            <td>{p.nit ? `${p.nit}${p.dv ? `-${p.dv}` : ""}` : "—"}</td>
            <td>{p.customer_group || "—"}</td>
            <td className="tabular-nums">{p.payment_terms_days} días</td>
            <td className="text-right tabular-nums">{amountText(p.credit_limit)}</td>
            <td>
              <Badge tone={p.status === "ACTIVE" ? "positive" : "neutral"}>{p.status === "ACTIVE" ? "Activo" : "Inactivo"}</Badge>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
