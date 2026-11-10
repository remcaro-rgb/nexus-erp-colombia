// Maestros › Bodegas y ubicaciones (F-INV-LOC): warehouse list + bin tree (WAI-ARIA tree) with stock per bin.
import { useState } from "react";
import type { LocationNodeDto } from "../api";
import { flattenTree, formatQty } from "../model";
import { useInventoryApi, useLoad } from "./useInventory";

function TreeLevel({ nodes, level }: { nodes: LocationNodeDto[]; level: number }) {
  return (
    <>
      {nodes.map((n) => (
        <li key={n.id} role="treeitem" aria-level={level} aria-expanded={n.children.length ? true : undefined} aria-selected={false}>
          <span>
            {n.code} — {n.name} · {n.location_type} · {formatQty(n.on_hand)}
            {n.status === "ARCHIVED" ? " (archivada)" : ""}
          </span>
          {n.children.length > 0 && (
            <ul role="group">
              <TreeLevel nodes={n.children} level={level + 1} />
            </ul>
          )}
        </li>
      ))}
    </>
  );
}

export default function WarehousesPage() {
  const api = useInventoryApi();
  const [warehouse, setWarehouse] = useState("");
  const [view, setView] = useState<"tree" | "table">("tree");
  const whs = useLoad((s) => api.warehouses(s), [api]);
  const tree = useLoad((s) => (warehouse ? api.tree(warehouse, s) : Promise.resolve({ warehouse_id: "", items: [] })), [api, warehouse]);
  const flat = flattenTree(tree.data?.items ?? []);
  return (
    <section className="nx-card flex flex-col gap-3" aria-labelledby="inv-wh-title">
      <h2 id="inv-wh-title" className="text-base font-semibold text-fg">Bodegas y ubicaciones</h2>
      <div className="flex gap-2">
        <label className="text-sm">
          Bodega
          <select value={warehouse} onChange={(e) => setWarehouse(e.target.value)} aria-label="Bodega">
            <option value="">Seleccione…</option>
            {whs.data?.items.map((w) => (
              <option key={w.id} value={w.id}>{`${w.code} — ${w.name}`}</option>
            ))}
          </select>
        </label>
        <button type="button" className="nx-btn" aria-pressed={view === "tree"} onClick={() => setView(view === "tree" ? "table" : "tree")}>
          {view === "tree" ? "Ver tabla" : "Ver árbol"}
        </button>
      </div>
      {tree.error && <p role="alert">{tree.error}</p>}
      {view === "tree" ? (
        <ul role="tree" aria-label="Árbol de ubicaciones">
          <TreeLevel nodes={tree.data?.items ?? []} level={1} />
        </ul>
      ) : (
        <table className="nx-table w-full text-sm">
          <caption className="sr-only">Ubicaciones de la bodega</caption>
          <thead>
            <tr>
              <th scope="col">Ruta</th>
              <th scope="col">Tipo</th>
              <th scope="col">Acepta movimientos</th>
              <th scope="col" className="text-right">Existencia</th>
            </tr>
          </thead>
          <tbody>
            {flat.map((r) => (
              <tr key={r.node.id}>
                <td style={{ paddingLeft: `${r.depth}rem` }}>{r.node.path}</td>
                <td>{r.node.location_type}</td>
                <td>{r.postable ? "Sí" : "No"}</td>
                <td className="text-right">{formatQty(r.node.on_hand)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
