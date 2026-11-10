// Inventario › Producción › Recetas / BOM (F-INV-BOM): versions, editor with yield/scrap, explosion preview with cost,
// activation with a validation report (server-side cycle / overlap checks surface as their stable error codes).
import { useMemo, useState } from "react";
import { useShell } from "@shell/ShellContext";
import { formatQty } from "../model";
import { errorText, useInventoryApi, useLoad } from "../pages/useInventory";
import { BomApi, type BomAction, type BomInput, type BomLineInput, type ExplosionDto } from "./api";
import { bomActions, flattenExplosion, groupVersions, validateBom } from "./model";

const ACTION_LABEL: Record<BomAction, string> = { activate: "Activar", obsolete: "Obsoleta", "copy-version": "Nueva versión" };
const today = () => new Date().toISOString().slice(0, 10);

function emptyInput(): BomInput {
  return { code: "", name: "", item_id: "", valid_from: today(), output_qty: "1", yield_percent: "100", components: { lines: [{ item_id: "", quantity: "1", scrap_percent: "0" }] } };
}

export default function RecipesPage() {
  const shell = useShell();
  const bomApi = useMemo(() => new BomApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
  const inv = useInventoryApi();
  const list = useLoad((s) => bomApi.list(s), [bomApi]);
  const items = useLoad((s) => inv.items("", s), [inv]);
  const [draft, setDraft] = useState<BomInput>(emptyInput);
  const [report, setReport] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [explosion, setExplosion] = useState<ExplosionDto | null>(null);
  const [preview, setPreview] = useState({ qty: "1", date: today() });

  function setLine(i: number, patch: Partial<BomLineInput>) {
    const lines = draft.components.lines.map((ln, k) => (k === i ? { ...ln, ...patch } : ln));
    setDraft({ ...draft, components: { ...draft.components, lines } });
  }

  async function save() {
    const errors = validateBom(draft);
    setReport(errors);
    if (errors.length) return;
    try {
      const b = await bomApi.create(draft);
      setMessage(`Receta ${b.code} v${b.version_no} creada en borrador`);
      setDraft(emptyInput());
      list.reload();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  async function act(id: string, action: BomAction) {
    try {
      const body = action === "activate" ? { supersede: true } : action === "copy-version" ? { valid_from: today() } : {};
      const b = await bomApi.action(id, action, body);
      setMessage(`Receta ${b.code} v${b.version_no}: ${b.bom_status}`);
      list.reload();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  async function explode(id: string) {
    try {
      setExplosion(await bomApi.explode(id, preview.qty, preview.date));
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  const itemOptions = items.data?.items ?? [];
  return (
    <section className="nx-card flex flex-col gap-4" aria-labelledby="inv-bom-title">
      <h2 id="inv-bom-title" className="text-base font-semibold text-fg">Recetas y listas de materiales</h2>
      {list.error && <p role="alert">{list.error}</p>}
      <table className="nx-table w-full text-sm">
        <caption className="sr-only">Versiones de recetas</caption>
        <thead>
          <tr>
            <th scope="col">Código</th>
            <th scope="col">Versión</th>
            <th scope="col">Estado</th>
            <th scope="col">Vigencia</th>
            <th scope="col">Rendimiento</th>
            <th scope="col">Acciones</th>
          </tr>
        </thead>
        <tbody>
          {groupVersions(list.data?.items ?? []).flatMap((g) =>
            g.versions.map((b) => (
              <tr key={b.id}>
                <td>{b.code}</td>
                <td>v{b.version_no}</td>
                <td>{b.bom_status}</td>
                <td>{b.valid_from} – {b.valid_to ?? "∞"}</td>
                <td>{formatQty(b.yield_percent)} %</td>
                <td className="flex gap-1">
                  {bomActions(b.bom_status).map((a) => (
                    <button key={a} type="button" className="nx-btn" onClick={() => act(b.id, a)} aria-label={`${ACTION_LABEL[a]} ${b.code} v${b.version_no}`}>
                      {ACTION_LABEL[a]}
                    </button>
                  ))}
                  <button type="button" className="nx-btn" onClick={() => explode(b.id)} aria-label={`Explosionar ${b.code} v${b.version_no}`}>
                    Explosión
                  </button>
                </td>
              </tr>
            )),
          )}
        </tbody>
      </table>

      <fieldset className="flex flex-wrap gap-2">
        <legend className="text-sm font-semibold">Vista previa de explosión</legend>
        <label>Cantidad <input className="nx-input" value={preview.qty} onChange={(e) => setPreview({ ...preview, qty: e.target.value })} inputMode="decimal" /></label>
        <label>Fecha <input className="nx-input" type="date" value={preview.date} onChange={(e) => setPreview({ ...preview, date: e.target.value })} /></label>
      </fieldset>
      {explosion && (
        <table className="nx-table w-full text-sm" aria-label="Explosión de la receta">
          <thead>
            <tr><th scope="col">Componente</th><th scope="col">Cantidad</th></tr>
          </thead>
          <tbody>
            {flattenExplosion(explosion.tree).map((r, i) => (
              <tr key={`${r.code}-${i}`}>
                <td style={{ paddingInlineStart: `${r.depth}rem` }}>{r.code}{r.byproduct ? " (subproducto)" : ""}</td>
                <td>{formatQty(r.quantity)}</td>
              </tr>
            ))}
          </tbody>
          {explosion.total_cost && (
            <tfoot><tr><th scope="row">Costo total</th><td>{formatQty(explosion.total_cost.amount)} COP</td></tr></tfoot>
          )}
        </table>
      )}

      <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void save(); }} aria-label="Nueva receta">
        <div className="flex flex-wrap gap-2">
          <label>Código <input className="nx-input" value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} required /></label>
          <label>Nombre <input className="nx-input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} required /></label>
          <label>Producto
            <select className="nx-input" value={draft.item_id} onChange={(e) => setDraft({ ...draft, item_id: e.target.value })} required>
              <option value="">—</option>
              {itemOptions.map((i) => <option key={i.id} value={i.id}>{i.code}</option>)}
            </select>
          </label>
          <label>Salida por lote <input className="nx-input" value={draft.output_qty} onChange={(e) => setDraft({ ...draft, output_qty: e.target.value })} inputMode="decimal" /></label>
          <label>Rendimiento % <input className="nx-input" value={draft.yield_percent} onChange={(e) => setDraft({ ...draft, yield_percent: e.target.value })} inputMode="decimal" /></label>
          <label>Vigente desde <input className="nx-input" type="date" value={draft.valid_from} onChange={(e) => setDraft({ ...draft, valid_from: e.target.value })} /></label>
        </div>
        {draft.components.lines.map((ln, i) => (
          <div key={i} className="flex flex-wrap gap-2" role="group" aria-label={`Componente ${i + 1}`}>
            <select className="nx-input" aria-label="Componente" value={ln.item_id} onChange={(e) => setLine(i, { item_id: e.target.value })}>
              <option value="">—</option>
              {itemOptions.map((it) => <option key={it.id} value={it.id}>{it.code}</option>)}
            </select>
            <input className="nx-input" aria-label="Cantidad por lote" value={ln.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} inputMode="decimal" />
            <input className="nx-input" aria-label="Desperdicio %" value={ln.scrap_percent ?? "0"} onChange={(e) => setLine(i, { scrap_percent: e.target.value })} inputMode="decimal" />
          </div>
        ))}
        <div className="flex gap-2">
          <button type="button" className="nx-btn" onClick={() => setDraft({ ...draft, components: { ...draft.components, lines: [...draft.components.lines, { item_id: "", quantity: "1", scrap_percent: "0" }] } })}>
            Agregar componente
          </button>
          <button type="submit" className="nx-btn nx-btn-primary">Guardar borrador</button>
        </div>
        {report.length > 0 && (
          <ul role="alert" aria-label="Reporte de validación">{report.map((r) => <li key={r}>{r}</li>)}</ul>
        )}
      </form>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
