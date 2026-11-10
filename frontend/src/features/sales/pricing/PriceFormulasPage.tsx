// Ventas › Clientes y precios › Fórmulas de precio (F-SAL-PRICING): bindings of approved P07 pricing formulas with their
// scope and priority, plus a price simulator (pricing:quote) for a customer/item/quantity. The formula text is edited in
// the shared P07 formula editor; this page never copies it.
import { useEffect, useState } from "react";
import { ApiError } from "@lib/api/client";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { formatCOP } from "@lib/money";
import type { PriceFormulaDto, QuotedPrice } from "../api";
import { PRICE_SOURCE_LABEL } from "../model";
import { useSalesApi } from "../useSalesApi";
import { PriceExplain } from "./PriceExplain";

function scopeText(scope: PriceFormulaDto["scope"]): string {
  const parts = Object.entries(scope).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`);
  return parts.length ? parts.join(" · ") : "Todos los ítems y clientes";
}

export default function PriceFormulasPage() {
  const { api, companyId } = useSalesApi();
  const [rows, setRows] = useState<PriceFormulaDto[] | null>(null);
  const [party, setParty] = useState("");
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("1");
  const [quoted, setQuoted] = useState<QuotedPrice | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setRows(null);
    api.priceFormulas().then(
      (p) => live && setRows(p.items),
      () => live && setRows([]),
    );
    return () => {
      live = false;
    };
  }, [api, companyId]);

  async function simulate() {
    setError(null);
    try {
      const res = await api.quotePrices(party.trim() || null, [{ item_id: item.trim(), quantity: qty.trim() || "1" }]);
      setQuoted(res.data.items[0] ?? null);
    } catch (e) {
      setQuoted(null);
      setError(e instanceof ApiError ? e.message : "No fue posible calcular el precio.");
    }
  }

  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  return (
    <div className="flex flex-col gap-4">
      {rows.length ? (
        <table className="nx-card w-full text-sm" aria-label="Fórmulas de precio">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th scope="col">Código</th>
              <th scope="col">Fórmula (P07)</th>
              <th scope="col">Alcance</th>
              <th scope="col">Prioridad</th>
              <th scope="col">Vigencia</th>
              <th scope="col">Estado</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <tr key={f.id}>
                <td>{f.code}</td>
                <td>{f.formula_code}</td>
                <td>{scopeText(f.scope)}</td>
                <td className="tabular-nums">{f.priority}</td>
                <td>
                  {f.valid_from} → {f.valid_to ?? "indefinida"}
                </td>
                <td>
                  <Badge tone={f.status === "ACTIVE" ? "positive" : "neutral"}>{f.status === "ACTIVE" ? "Activa" : "Archivada"}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState title="Sin fórmulas de precio" body="Las fórmulas aprobadas en el editor de fórmulas se vinculan aquí con un alcance y una prioridad." />
      )}
      <section className="nx-card flex flex-col gap-2 p-3" aria-label="Simular precio">
        <h3 className="text-sm font-semibold">Simular precio</h3>
        <div className="flex flex-wrap gap-2">
          <input className="nx-input" aria-label="Cliente (id)" placeholder="Cliente (id)" value={party} onChange={(e) => setParty(e.target.value)} />
          <input className="nx-input" aria-label="Ítem (id)" placeholder="Ítem (id)" value={item} onChange={(e) => setItem(e.target.value)} />
          <input className="nx-input w-24" aria-label="Cantidad" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
          <Button onClick={simulate} disabled={!item.trim()}>
            Calcular
          </Button>
        </div>
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        {quoted && (
          <div className="text-sm">
            <p>
              {PRICE_SOURCE_LABEL[quoted.source]}:{" "}
              <strong className="tabular-nums">
                {quoted.unit_price.currency === "COP" ? formatCOP(quoted.unit_price.amount) : `${quoted.unit_price.amount} ${quoted.unit_price.currency}`}
              </strong>
            </p>
            <PriceExplain snapshot={quoted.explain} />
          </div>
        )}
      </section>
    </div>
  );
}
