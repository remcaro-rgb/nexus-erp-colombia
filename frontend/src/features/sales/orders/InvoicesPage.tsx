// Ventas › Facturas de venta: drafts can be repriced (preview diff, then apply) and posted; posted invoices are never
// repriced (D-024) and show their DIAN status (P15 e-invoicing reads the posted event).
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@lib/api/client";
import { Button } from "@ds/components/Button";
import { Dialog } from "@ds/components/Dialog";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { formatCOP } from "@lib/money";
import type { RepriceDiff, SalesDocumentDto } from "../api";
import { canReprice, repriceSummary } from "../model";
import { useSalesApi } from "../useSalesApi";
import { DocumentsTable } from "./DocumentsTable";

export default function InvoicesPage() {
  const { api, companyId, can } = useSalesApi();
  const [rows, setRows] = useState<SalesDocumentDto[] | null>(null);
  const [preview, setPreview] = useState<{ id: string; diff: RepriceDiff[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setRows(null);
    return api.invoices().then(
      (p) => setRows(p.items),
      () => setRows([]),
    );
  }, [api]);
  useEffect(() => {
    void load();
  }, [load, companyId]);

  async function run(fn: () => Promise<unknown>, message: string) {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : message);
    }
  }

  async function previewReprice(id: string) {
    setError(null);
    try {
      const doc = await api.reprice("invoices", id, false);
      setPreview({ id, diff: doc.reprice_diff ?? [] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No fue posible recalcular los precios.");
    }
  }

  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!rows.length) return <EmptyState title="Aún no hay facturas de venta" body="Las facturas nacen de un pedido entregado o se crean manualmente." />;
  const summary = preview ? repriceSummary(preview.diff) : null;
  return (
    <div className="flex flex-col gap-2">
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      <DocumentsTable
        rows={rows}
        label="Facturas de venta"
        actions={(doc) =>
          canReprice(doc) ? (
            <div className="flex flex-col gap-1">
              {can("sales.invoice.update") && <Button onClick={() => void previewReprice(doc.id)}>Recalcular precios</Button>}
              {can("sales.invoice.post") && <Button onClick={() => void run(() => api.postInvoice(doc.id), "No fue posible contabilizar la factura.")}>Contabilizar</Button>}
            </div>
          ) : null
        }
      />
      {preview && summary && (
        <Dialog open title="Recalcular precios del borrador" onOpenChange={(o) => !o && setPreview(null)}>
          <p className="text-sm">
            {summary.changed} línea(s) cambian · variación {formatCOP(summary.delta)}
          </p>
          <ul className="text-sm">
            {preview.diff.map((d) => (
              <li key={d.line_no}>
                Línea {d.line_no}: {d.old.unit_price} → {d.new.unit_price} {d.changed ? "" : "(sin cambio)"}
              </li>
            ))}
          </ul>
          <Button
            onClick={() =>
              void run(async () => {
                await api.reprice("invoices", preview.id, true);
                setPreview(null);
              }, "No fue posible aplicar los nuevos precios.")
            }
            disabled={!summary.changed}
          >
            Aplicar
          </Button>
        </Dialog>
      )}
    </div>
  );
}
