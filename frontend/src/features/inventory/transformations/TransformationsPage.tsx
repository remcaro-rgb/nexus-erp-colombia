// Inventario › Producción › Transformaciones (F-INV-XFORM): tabs Presentación · Porcionado · Manual · Producción;
// document with inputs/outputs/waste grid, live base-quantity balance, simulate preview (value balance, tolerance,
// journal), reserve → submit → approve (with reason) → post → reverse.
import { useMemo, useState } from "react";
import { useShell } from "@shell/ShellContext";
import { formatQty } from "../model";
import { errorText, useInventoryApi, useLoad } from "../pages/useInventory";
import { TransformationApi, type LineRole, type SimulationDto, type TransformationAction, type TransformationLineInput, type TransformationType } from "./api";
import { quantityBalance, transformationActions, TYPE_TABS } from "./model";

const ACTION_LABEL: Record<TransformationAction, string> = {
  reserve: "Reservar", submit: "Enviar", approve: "Aprobar", reject: "Rechazar", cancel: "Anular", post: "Contabilizar", reverse: "Reversar",
};
const NEEDS_REASON: TransformationAction[] = ["approve", "reject", "reverse"];

export default function TransformationsPage() {
  const shell = useShell();
  const api = useMemo(() => new TransformationApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
  const inv = useInventoryApi();
  const [tab, setTab] = useState<TransformationType>("PORTIONING");
  const list = useLoad((s) => api.list(tab, s), [api, tab]);
  const warehouses = useLoad((s) => inv.warehouses(s), [inv]);
  const items = useLoad((s) => inv.items("", s), [inv]);
  const [warehouseId, setWarehouseId] = useState("");
  const [lines, setLines] = useState<TransformationLineInput[]>([{ line_no: 1, role: "INPUT", quantity: "1" }]);
  const [simulation, setSimulation] = useState<SimulationDto | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const balance = quantityBalance(lines);

  function setLine(i: number, patch: Partial<TransformationLineInput>) {
    setLines(lines.map((ln, k) => (k === i ? { ...ln, ...patch } : ln)));
  }

  async function create() {
    try {
      const o = await api.create({ transformation_type: tab, warehouse_id: warehouseId, lines });
      setMessage(`Transformación ${o.number} creada (${o.status})`);
      list.reload();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  async function act(id: string, number: string, action: TransformationAction) {
    let reason: string | undefined;
    if (NEEDS_REASON.includes(action)) {
      reason = window.prompt(`Motivo para ${ACTION_LABEL[action].toLowerCase()} ${number}`) ?? undefined;
      if (!reason) return;
    }
    try {
      const o = await api.action(id, action, reason);
      setMessage(`Transformación ${o.number}: ${o.status}`);
      list.reload();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  async function simulate(id: string) {
    try {
      setSimulation(await api.simulate(id));
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  return (
    <section className="nx-card flex flex-col gap-4" aria-labelledby="inv-xf-title">
      <h2 id="inv-xf-title" className="text-base font-semibold text-fg">Transformaciones</h2>
      <div role="tablist" aria-label="Tipo de transformación" className="flex gap-1">
        {TYPE_TABS.map((t) => (
          <button key={t.type} role="tab" type="button" aria-selected={tab === t.type} className="nx-btn" onClick={() => setTab(t.type)}>
            {t.label}
          </button>
        ))}
      </div>
      {list.error && <p role="alert">{list.error}</p>}
      <table className="nx-table w-full text-sm">
        <caption className="sr-only">Transformaciones</caption>
        <thead>
          <tr>
            <th scope="col">Número</th>
            <th scope="col">Fecha</th>
            <th scope="col">Estado</th>
            <th scope="col">Valor</th>
            <th scope="col">Acciones</th>
          </tr>
        </thead>
        <tbody>
          {(list.data?.items ?? []).map((o) => (
            <tr key={o.id}>
              <td>{o.number}</td>
              <td>{o.document_date}</td>
              <td>{o.status}{o.approval_required ? " · requiere aprobación" : ""}</td>
              <td>{o.value_out ? `${formatQty(o.value_out.amount)} COP` : "—"}</td>
              <td className="flex gap-1">
                <button type="button" className="nx-btn" onClick={() => simulate(o.id)} aria-label={`Simular ${o.number}`}>Simular</button>
                {transformationActions(o.status, o.inputs_reserved).map((a) => (
                  <button key={a} type="button" className="nx-btn" onClick={() => act(o.id, o.number, a)} aria-label={`${ACTION_LABEL[a]} ${o.number}`}>
                    {ACTION_LABEL[a]}
                  </button>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {simulation && (
        <div role="region" aria-label="Simulación" className="flex flex-col gap-1 text-sm">
          <p>Balance de valor: {simulation.balanced ? "cuadra" : "no cuadra"} · Base: {formatQty(simulation.qty_in)} → {formatQty(simulation.qty_out)}</p>
          {simulation.value_in && <p>Valor entrada {formatQty(simulation.value_in.amount)} · salida {formatQty(simulation.value_out?.amount ?? "0")} · merma gasto {formatQty(simulation.waste_expensed?.amount ?? "0")}</p>}
          <p>{simulation.approval_required ? `Requiere aprobación: ${simulation.approval_reasons.join("; ")}` : "Dentro de tolerancia"}</p>
          {simulation.journal && simulation.journal.length > 0 && (
            <ul aria-label="Asiento previsto">
              {simulation.journal.map((j, i) => <li key={i}>{j.side === "DEBIT" ? "Db" : "Cr"} {j.account_role} {formatQty(j.amount.amount)}</li>)}
            </ul>
          )}
        </div>
      )}

      <form className="flex flex-col gap-2" aria-label="Nueva transformación" onSubmit={(e) => { e.preventDefault(); void create(); }}>
        <label>Bodega
          <select className="nx-input" value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)} required>
            <option value="">—</option>
            {(warehouses.data?.items ?? []).map((w) => <option key={w.id} value={w.id}>{w.code}</option>)}
          </select>
        </label>
        {lines.map((ln, i) => (
          <div key={ln.line_no} className="flex flex-wrap gap-2" role="group" aria-label={`Línea ${ln.line_no}`}>
            <select className="nx-input" aria-label="Rol" value={ln.role} onChange={(e) => setLine(i, { role: e.target.value as LineRole })}>
              <option value="INPUT">Insumo</option>
              <option value="OUTPUT">Salida</option>
              <option value="WASTE">Merma</option>
            </select>
            <select className="nx-input" aria-label="Producto" value={ln.item_id ?? ""} onChange={(e) => setLine(i, { item_id: e.target.value || undefined })}>
              <option value="">—</option>
              {(items.data?.items ?? []).map((it) => <option key={it.id} value={it.id}>{it.code}</option>)}
            </select>
            <input className="nx-input" aria-label="Cantidad" value={ln.quantity ?? ""} onChange={(e) => setLine(i, { quantity: e.target.value })} inputMode="decimal" />
            {ln.role === "OUTPUT" && (
              <input className="nx-input" aria-label="Peso real" value={ln.actual_weight ?? ""} onChange={(e) => setLine(i, { actual_weight: e.target.value || undefined })} inputMode="decimal" />
            )}
            {ln.role === "WASTE" && (
              <label><input type="checkbox" checked={Boolean(ln.abnormal)} onChange={(e) => setLine(i, { abnormal: e.target.checked })} /> Anormal</label>
            )}
          </div>
        ))}
        <p role="status" aria-live="polite">
          Balance de cantidades: entrada {formatQty(balance.input)} = salida {formatQty(balance.output)} + merma {formatQty(balance.waste)}
          {balance.balanced ? " ✓" : ` (diferencia ${formatQty(balance.difference)})`}
        </p>
        <div className="flex gap-2">
          <button type="button" className="nx-btn" onClick={() => setLines([...lines, { line_no: lines.length + 1, role: "OUTPUT", quantity: "0" }])}>Agregar línea</button>
          <button type="submit" className="nx-btn nx-btn-primary">Crear borrador</button>
        </div>
      </form>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
