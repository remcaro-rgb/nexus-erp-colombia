// Movimientos › Entre ubicaciones (F-INV-LOC): bin-to-bin move inside one warehouse (no journal entry).
import { useState, type FormEvent } from "react";
import { flattenTree, validateMove } from "../model";
import { errorText, useInventoryApi, useLoad } from "./useInventory";

export default function InternalMovePage() {
  const api = useInventoryApi();
  const [warehouse, setWarehouse] = useState("");
  const [form, setForm] = useState({ item_id: "", from_location_id: "", to_location_id: "", quantity: "" });
  const [message, setMessage] = useState<string | null>(null);
  const whs = useLoad((s) => api.warehouses(s), [api]);
  const items = useLoad((s) => api.items("", s), [api]);
  const tree = useLoad((s) => (warehouse ? api.tree(warehouse, s) : Promise.resolve({ warehouse_id: "", items: [] })), [api, warehouse]);
  const bins = flattenTree(tree.data?.items ?? []).filter((r) => r.postable && r.node.location_type === "INTERNAL");

  async function submit(e: FormEvent) {
    e.preventDefault();
    const problem = validateMove(form);
    if (problem) {
      setMessage(problem);
      return;
    }
    try {
      await api.internalMove(form);
      setMessage("Movimiento registrado");
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  const binSelect = (key: "from_location_id" | "to_location_id", label: string) => (
    <label className="text-sm">
      {label}
      <select value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} aria-label={label}>
        <option value="">Seleccione…</option>
        {bins.map((b) => (
          <option key={b.node.id} value={b.node.id}>{b.node.path}</option>
        ))}
      </select>
    </label>
  );

  return (
    <section className="nx-card flex flex-col gap-3" aria-labelledby="inv-move-title">
      <h2 id="inv-move-title" className="text-base font-semibold text-fg">Mover entre ubicaciones</h2>
      <form onSubmit={submit} className="grid grid-cols-2 gap-2" aria-label="Mover entre ubicaciones">
        <label className="text-sm">
          Bodega
          <select value={warehouse} onChange={(e) => setWarehouse(e.target.value)} aria-label="Bodega">
            <option value="">Seleccione…</option>
            {whs.data?.items.map((w) => (
              <option key={w.id} value={w.id}>{`${w.code} — ${w.name}`}</option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          Producto
          <select value={form.item_id} onChange={(e) => setForm({ ...form, item_id: e.target.value })} aria-label="Producto">
            <option value="">Seleccione…</option>
            {items.data?.items.map((i) => (
              <option key={i.id} value={i.id}>{`${i.code} — ${i.name}`}</option>
            ))}
          </select>
        </label>
        {binSelect("from_location_id", "Ubicación origen")}
        {binSelect("to_location_id", "Ubicación destino")}
        <label className="text-sm">
          Cantidad (unidad base)
          <input value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} inputMode="decimal" required />
        </label>
        <button type="submit" className="nx-btn">Mover</button>
      </form>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
