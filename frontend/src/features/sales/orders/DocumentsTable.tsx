// Shared table of sales documents (orders, invoices) with status, totals and the per-line price explanation.
import type { ReactNode } from "react";
import { Badge } from "@ds/components/Badge";
import { formatCOP } from "@lib/money";
import type { Money, SalesDocumentDto } from "../api";
import { DOCUMENT_STATUS_LABEL, OVERRIDE_LABEL, pendingOverride } from "../model";
import { PriceExplain } from "../pricing/PriceExplain";

export function amountText(m: Money | null | undefined): string {
  if (!m) return "—";
  return m.currency === "COP" ? formatCOP(m.amount) : `${m.amount} ${m.currency}`;
}

export function DocumentsTable({ rows, label, actions }: { rows: SalesDocumentDto[]; label: string; actions?: (doc: SalesDocumentDto) => ReactNode }) {
  return (
    <table className="nx-card w-full text-sm" aria-label={label}>
      <thead>
        <tr className="text-left text-xs text-muted">
          <th scope="col">Número</th>
          <th scope="col">Fecha</th>
          <th scope="col">Cliente</th>
          <th scope="col">Estado</th>
          <th scope="col">Total</th>
          <th scope="col">Líneas</th>
          {actions && <th scope="col">Acciones</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((doc) => (
          <tr key={doc.id} className="align-top">
            <td>{doc.number}</td>
            <td>{doc.document_date}</td>
            <td>{doc.party?.name ?? "—"}</td>
            <td>
              <Badge tone={pendingOverride(doc) ? "warning" : "neutral"}>{DOCUMENT_STATUS_LABEL[doc.document_status] ?? doc.document_status}</Badge>
              {doc.fiscal_status && <span className="ml-1 text-xs text-muted">DIAN: {doc.fiscal_status}</span>}
            </td>
            <td className="text-right tabular-nums">{amountText(doc.total)}</td>
            <td>
              <ul className="flex flex-col gap-1">
                {doc.lines.map((l) => (
                  <li key={l.id}>
                    <span className="tabular-nums">
                      {l.quantity.value} × {amountText(l.unit_price)}
                    </span>
                    {l.override_status !== "NONE" && <span className="ml-1 text-xs text-muted">{OVERRIDE_LABEL[l.override_status]}</span>}
                    <PriceExplain snapshot={l.price_snapshot} />
                  </li>
                ))}
              </ul>
            </td>
            {actions && <td>{actions(doc)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
