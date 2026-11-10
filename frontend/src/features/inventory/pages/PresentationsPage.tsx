// Maestros › Productos y presentaciones (F-INV-PRES): list and create presentations of an item.
import { useState, type FormEvent } from "react";
import { formatQty } from "../model";
import { errorText, useInventoryApi, useLoad } from "./useInventory";

export default function PresentationsPage() {
  const api = useInventoryApi();
  const [item, setItem] = useState("");
  const [form, setForm] = useState({ code: "", name: "", uom: "", factor: "", barcode: "" });
  const [message, setMessage] = useState<string | null>(null);
  const items = useLoad((s) => api.items("", s), [api]);
  const list = useLoad((s) => (item ? api.presentations(item, s) : Promise.resolve({ items: [], next_cursor: null })), [api, item]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.createPresentation(item, { ...form, barcode: form.barcode || null });
      setMessage("Presentación creada");
      setForm({ code: "", name: "", uom: "", factor: "", barcode: "" });
      list.reload();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  return (
    <section className="nx-card flex flex-col gap-3" aria-labelledby="inv-presm-title">
      <h2 id="inv-presm-title" className="text-base font-semibold text-fg">Presentaciones</h2>
      <label className="text-sm">
        Producto
        <select value={item} onChange={(e) => setItem(e.target.value)} aria-label="Producto">
          <option value="">Seleccione…</option>
          {items.data?.items.map((i) => (
            <option key={i.id} value={i.id}>{`${i.code} — ${i.name} (${i.base_uom})`}</option>
          ))}
        </select>
      </label>
      <table className="nx-table w-full text-sm">
        <caption className="sr-only">Presentaciones del producto</caption>
        <thead>
          <tr>
            <th scope="col">Código</th>
            <th scope="col">Nombre</th>
            <th scope="col" className="text-right">Factor</th>
            <th scope="col">Código de barras</th>
            <th scope="col">Estado</th>
          </tr>
        </thead>
        <tbody>
          {(list.data?.items ?? []).map((p) => (
            <tr key={p.id}>
              <td>{p.code}</td>
              <td>{p.name}</td>
              <td className="text-right">{formatQty(p.factor)}</td>
              <td>{p.barcode ?? "—"}</td>
              <td>{p.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {item && (
        <form onSubmit={submit} className="grid grid-cols-2 gap-2" aria-label="Nueva presentación">
          {(["code", "name", "uom", "factor", "barcode"] as const).map((k) => (
            <label key={k} className="text-sm">
              {{ code: "Código", name: "Nombre", uom: "Unidad de empaque", factor: "Factor a unidad base", barcode: "Código de barras" }[k]}
              <input value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} required={k !== "barcode"} inputMode={k === "factor" ? "decimal" : undefined} />
            </label>
          ))}
          <button type="submit" className="nx-btn">Crear presentación</button>
        </form>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
