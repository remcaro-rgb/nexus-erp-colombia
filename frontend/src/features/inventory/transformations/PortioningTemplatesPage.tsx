// Inventario › Producción › Plantillas de porcionado (F-INV-XFORM, D-015): outputs with expected yield %, cost share %,
// waste % and tolerance % (default ±2 %).
import { useMemo, useState } from "react";
import { useShell } from "@shell/ShellContext";
import { formatQty, fromScaled, toScaled } from "../model";
import { errorText, useInventoryApi, useLoad } from "../pages/useInventory";
import { TransformationApi, type TemplateInput } from "./api";

function empty(): TemplateInput {
  return { code: "", name: "", input_item_id: "", tolerance_percent: "2", waste_percent: "0", outputs: { lines: [{ item_id: "", expected_yield_percent: "0", is_main: true }] } };
}

export default function PortioningTemplatesPage() {
  const shell = useShell();
  const api = useMemo(() => new TransformationApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
  const inv = useInventoryApi();
  const list = useLoad((s) => api.templates(s), [api]);
  const items = useLoad((s) => inv.items("", s), [inv]);
  const [draft, setDraft] = useState<TemplateInput>(empty);
  const [message, setMessage] = useState<string | null>(null);
  const total = draft.outputs.lines.reduce((acc, o) => acc + toScaled(o.expected_yield_percent || "0"), toScaled(draft.waste_percent || "0"));
  const itemOptions = items.data?.items ?? [];

  function setOut(i: number, patch: Partial<TemplateInput["outputs"]["lines"][number]>) {
    setDraft({ ...draft, outputs: { lines: draft.outputs.lines.map((o, k) => (k === i ? { ...o, ...patch } : o)) } });
  }

  async function save() {
    try {
      const t = await api.createTemplate(draft);
      setMessage(`Plantilla ${t.code} creada`);
      setDraft(empty());
      list.reload();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  return (
    <section className="nx-card flex flex-col gap-4" aria-labelledby="inv-tpl-title">
      <h2 id="inv-tpl-title" className="text-base font-semibold text-fg">Plantillas de porcionado</h2>
      {list.error && <p role="alert">{list.error}</p>}
      <table className="nx-table w-full text-sm">
        <caption className="sr-only">Plantillas</caption>
        <thead>
          <tr><th scope="col">Código</th><th scope="col">Nombre</th><th scope="col">Salidas</th><th scope="col">Tolerancia</th></tr>
        </thead>
        <tbody>
          {(list.data?.items ?? []).map((t) => (
            <tr key={t.id}>
              <td>{t.code}</td>
              <td>{t.name}</td>
              <td>{t.outputs.lines.map((o) => `${o.item_code} ${formatQty(o.expected_yield_percent)} %`).join(" · ")}</td>
              <td>± {formatQty(t.tolerance_percent)} %</td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="flex flex-col gap-2" aria-label="Nueva plantilla" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <div className="flex flex-wrap gap-2">
          <label>Código <input className="nx-input" value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} required /></label>
          <label>Nombre <input className="nx-input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} required /></label>
          <label>Insumo
            <select className="nx-input" value={draft.input_item_id} onChange={(e) => setDraft({ ...draft, input_item_id: e.target.value })} required>
              <option value="">—</option>
              {itemOptions.map((i) => <option key={i.id} value={i.id}>{i.code}</option>)}
            </select>
          </label>
          <label>Merma % <input className="nx-input" value={draft.waste_percent} onChange={(e) => setDraft({ ...draft, waste_percent: e.target.value })} inputMode="decimal" /></label>
          <label>Tolerancia % <input className="nx-input" value={draft.tolerance_percent} onChange={(e) => setDraft({ ...draft, tolerance_percent: e.target.value })} inputMode="decimal" /></label>
        </div>
        {draft.outputs.lines.map((o, i) => (
          <div key={i} className="flex flex-wrap gap-2" role="group" aria-label={`Salida ${i + 1}`}>
            <select className="nx-input" aria-label="Producto" value={o.item_id} onChange={(e) => setOut(i, { item_id: e.target.value })}>
              <option value="">—</option>
              {itemOptions.map((it) => <option key={it.id} value={it.id}>{it.code}</option>)}
            </select>
            <input className="nx-input" aria-label="Rendimiento esperado %" value={o.expected_yield_percent} onChange={(e) => setOut(i, { expected_yield_percent: e.target.value })} inputMode="decimal" />
            <input className="nx-input" aria-label="Participación en costo %" value={o.cost_share_percent ?? ""} onChange={(e) => setOut(i, { cost_share_percent: e.target.value || undefined })} inputMode="decimal" />
          </div>
        ))}
        <p role="status">Rendimientos + merma: {formatQty(fromScaled(total))} %{total > toScaled("100") ? " (supera 100 %)" : ""}</p>
        <div className="flex gap-2">
          <button type="button" className="nx-btn" onClick={() => setDraft({ ...draft, outputs: { lines: [...draft.outputs.lines, { item_id: "", expected_yield_percent: "0" }] } })}>Agregar salida</button>
          <button type="submit" className="nx-btn nx-btn-primary">Guardar</button>
        </div>
      </form>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
