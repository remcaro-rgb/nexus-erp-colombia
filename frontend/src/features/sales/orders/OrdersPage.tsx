// Ventas › Pedidos: sales orders with status (credit / price approvals), totals and confirmation.
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@lib/api/client";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import type { SalesDocumentDto } from "../api";
import { useSalesApi } from "../useSalesApi";
import { DocumentsTable } from "./DocumentsTable";

export default function OrdersPage() {
  const { api, companyId, can } = useSalesApi();
  const [rows, setRows] = useState<SalesDocumentDto[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setRows(null);
    return api.orders().then(
      (p) => setRows(p.items),
      () => setRows([]),
    );
  }, [api]);
  useEffect(() => {
    void load();
  }, [load, companyId]);

  async function confirm(id: string) {
    setError(null);
    try {
      await api.orderAction(id, "confirm");
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No fue posible confirmar el pedido.");
    }
  }

  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!rows.length) return <EmptyState title="Aún no hay pedidos de venta" body="Los pedidos nacen de una cotización aceptada o se crean manualmente." />;
  return (
    <div className="flex flex-col gap-2">
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      <DocumentsTable
        rows={rows}
        label="Pedidos de venta"
        actions={(doc) =>
          (doc.document_status === "DRAFT" || doc.document_status === "PENDING_APPROVAL") && can("sales.order.update") ? (
            <Button onClick={() => void confirm(doc.id)}>Confirmar</Button>
          ) : null
        }
      />
    </div>
  );
}
