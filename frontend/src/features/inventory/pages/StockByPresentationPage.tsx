// Existencias › Por presentación (F-INV-PRES): packaged per presentation + loose, never double counted (D-013).
import { useState } from "react";
import { formatQty, totalsByPresentation } from "../model";
import { useInventoryApi, useLoad } from "./useInventory";

export default function StockByPresentationPage() {
  const api = useInventoryApi();
  const [q, setQ] = useState("");
  const [item, setItem] = useState("");
  const items = useLoad((s) => api.items(q, s), [api, q]);
  const rows = useLoad((s) => (item ? api.availability({ item }, s) : Promise.resolve({ items: [], next_cursor: null })), [api, item]);
  const totals = totalsByPresentation(rows.data?.items ?? []);
  return (
    <section className="nx-card flex flex-col gap-3" aria-labelledby="inv-pres-title">
      <h2 id="inv-pres-title" className="text-base font-semibold text-fg">Existencias por presentación</h2>
      <div className="flex gap-2">
        <label className="text-sm">
          Buscar producto <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar producto" />
        </label>
        <label className="text-sm">
          Producto
          <select value={item} onChange={(e) => setItem(e.target.value)} aria-label="Producto">
            <option value="">Seleccione…</option>
            {items.data?.items.map((i) => (
              <option key={i.id} value={i.id}>{`${i.code} — ${i.name}`}</option>
            ))}
          </select>
        </label>
      </div>
      {rows.error && <p role="alert">{rows.error}</p>}
      <table className="nx-table w-full text-sm">
        <caption className="sr-only">Stock empacado por presentación y suelto</caption>
        <thead>
          <tr>
            <th scope="col">Presentación</th>
            <th scope="col" className="text-right">Factor</th>
            <th scope="col" className="text-right">Empaques</th>
            <th scope="col" className="text-right">Empaques disponibles</th>
            <th scope="col" className="text-right">Unidades base</th>
          </tr>
        </thead>
        <tbody>
          {totals.presentations.map((p) => (
            <tr key={p.presentation_id}>
              <td>{p.code}</td>
              <td className="text-right">{formatQty(p.factor)}</td>
              <td className="text-right">{formatQty(p.packages)}</td>
              <td className="text-right">{formatQty(p.available_packages)}</td>
              <td className="text-right">{formatQty(p.base)}</td>
            </tr>
          ))}
          <tr>
            <td>Suelto</td>
            <td />
            <td />
            <td />
            <td className="text-right">{formatQty(totals.loose)}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Existencia canónica</th>
            <td colSpan={4} className="text-right">{formatQty(totals.canonical)}</td>
          </tr>
        </tfoot>
      </table>
    </section>
  );
}
