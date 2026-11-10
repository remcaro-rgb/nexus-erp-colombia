// Existencias › Por ubicación (F-INV-LOC): on hand / reserved / available per bin and lot, filtered by warehouse.
import { useState } from "react";
import { formatQty } from "../model";
import { useInventoryApi, useLoad } from "./useInventory";

export default function StockByLocationPage() {
  const api = useInventoryApi();
  const [warehouse, setWarehouse] = useState("");
  const whs = useLoad((s) => api.warehouses(s), [api]);
  const rows = useLoad((s) => api.availability(warehouse ? { warehouse } : {}, s), [api, warehouse]);
  return (
    <section className="nx-card flex flex-col gap-3" aria-labelledby="inv-loc-title">
      <h2 id="inv-loc-title" className="text-base font-semibold text-fg">Existencias por ubicación</h2>
      <label className="flex items-center gap-2 text-sm">
        Bodega
        <select value={warehouse} onChange={(e) => setWarehouse(e.target.value)} aria-label="Bodega">
          <option value="">Todas</option>
          {whs.data?.items.map((w) => (
            <option key={w.id} value={w.id}>{`${w.code} — ${w.name}`}</option>
          ))}
        </select>
      </label>
      {rows.error && <p role="alert">{rows.error}</p>}
      <table className="nx-table w-full text-sm">
        <caption className="sr-only">Existencias por ubicación y lote</caption>
        <thead>
          <tr>
            <th scope="col">Ubicación</th>
            <th scope="col">Tipo</th>
            <th scope="col">Lote</th>
            <th scope="col" className="text-right">Existencia</th>
            <th scope="col" className="text-right">Reservado</th>
            <th scope="col" className="text-right">Disponible</th>
          </tr>
        </thead>
        <tbody>
          {(rows.data?.items ?? []).map((r) => (
            <tr key={`${r.item_id}-${r.location_id}-${r.lot_id ?? "-"}`}>
              <td>{r.location_code}</td>
              <td>{r.location_type === "TRANSIT" ? "En tránsito" : r.location_type}</td>
              <td>{r.lot_id ?? "—"}</td>
              <td className="text-right">{formatQty(r.on_hand)}</td>
              <td className="text-right">{formatQty(r.reserved)}</td>
              <td className="text-right">{formatQty(r.available)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
