// Tesorería › Recibos de caja › Conceptos de tesorería (F-TRE-RC): concepts with direction and GL role.
import { useEffect, useState } from "react";
import { Badge } from "@ds/components/Badge";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import type { ConceptDto } from "../api";
import { useTreasuryApi } from "../useTreasuryApi";

const DIRECTION = { IN: "Ingreso", OUT: "Egreso", BOTH: "Ambos" } as const;

export default function ConceptsPage() {
  const { api, companyId } = useTreasuryApi();
  const [rows, setRows] = useState<ConceptDto[] | null>(null);
  useEffect(() => {
    let live = true;
    setRows(null);
    api.concepts().then(
      (p) => live && setRows(p.items),
      () => live && setRows([]),
    );
    return () => {
      live = false;
    };
  }, [api, companyId]);
  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!rows.length) return <EmptyState title="Sin conceptos de tesorería" body="Cree conceptos (préstamos, reintegros, gastos) con su cuenta contable." />;
  return (
    <table className="nx-card w-full text-sm" aria-label="Conceptos de tesorería">
      <thead>
        <tr className="text-left text-xs text-muted">
          <th scope="col">Código</th>
          <th scope="col">Nombre</th>
          <th scope="col">Dirección</th>
          <th scope="col">Rol contable</th>
          <th scope="col">Estado</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => (
          <tr key={c.id}>
            <td>{c.code}</td>
            <td>{c.name}</td>
            <td>{DIRECTION[c.direction]}</td>
            <td>{c.account_role}</td>
            <td>
              <Badge tone={c.status === "ACTIVE" ? "positive" : "neutral"}>{c.status === "ACTIVE" ? "Activo" : "Archivado"}</Badge>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
