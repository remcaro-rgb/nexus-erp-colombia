// Inventario › Movimientos › Salidas (F-INV-ISSUE): issues by concept (consumo, consumo directo, baja, muestra, otro).
// Lines are filled by scanning (ScanInput) or by item id; the form pre-validates dimensions/reason like the server,
// simulate previews the journal without posting, and every action goes through the public API (server re-checks).
import { useEffect, useMemo, useState } from "react";
import { useShell } from "@shell/ShellContext";
import { ApiError } from "@lib/api/client";
import { InventoryApi, type WarehouseDto } from "../api";
import { BarcodesApi, type ScanResolution } from "../barcodes/api";
import ScanInput from "../barcodes/ScanInput";
import { IssuesApi, type IssueAction, type IssueConceptDto, type IssueDto, type IssueLineInput, type SimulationDto } from "./api";
import { DIMENSION_LABEL, SLOT, STATUS_LABEL, formatCop, issueActions, requiredDimensions, validateDraft } from "./model";

const ACTION_LABEL: Record<IssueAction, string> = {
  simulate: "Simular",
  submit: "Enviar a aprobación",
  approve: "Aprobar",
  post: "Contabilizar",
  reverse: "Reversar",
};

function errorText(e: unknown): string {
  return e instanceof ApiError ? `${e.code}: ${e.message}` : String(e);
}

export default function IssuesPage() {
  const shell = useShell();
  const api = useMemo(() => new IssuesApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
  const barcodes = useMemo(() => new BarcodesApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
  const inv = useMemo(() => new InventoryApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
  const [concepts, setConcepts] = useState<IssueConceptDto[]>([]);
  const [warehouses, setWarehouses] = useState<WarehouseDto[]>([]);
  const [issues, setIssues] = useState<IssueDto[]>([]);
  const [conceptId, setConceptId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [reason, setReason] = useState("");
  const [dims, setDims] = useState<Record<string, string>>({});
  const [lines, setLines] = useState<IssueLineInput[]>([]);
  const [itemId, setItemId] = useState("");
  const [qty, setQty] = useState("1");
  const [sim, setSim] = useState<SimulationDto | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const concept = concepts.find((c) => c.id === conceptId) ?? null;

  async function reload(signal?: AbortSignal) {
    try {
      const [c, w, i] = await Promise.all([api.concepts(signal), inv.warehouses(signal), api.issues({}, signal)]);
      setConcepts(c.items.filter((x) => x.status === "ACTIVE"));
      setWarehouses(w.items);
      setIssues(i.items);
    } catch (e) {
      if (!signal?.aborted) setMessage(errorText(e));
    }
  }

  useEffect(() => {
    const ctl = new AbortController();
    void reload(ctl.signal);
    return () => ctl.abort();
  }, [api, inv]);

  function onScan(r: ScanResolution) {
    if (r.owner_type === "location") {
      setMessage(`Ubicación ${r.code ?? ""} seleccionada para la siguiente línea`);
      setDims((d) => ({ ...d, __location: r.location_id ?? "" }));
      return;
    }
    if (!r.item_id) return;
    const line: IssueLineInput = r.presentation_id
      ? { item_id: r.item_id, qty_packages: qty, presentation_id: r.presentation_id }
      : { item_id: r.item_id, quantity: qty };
    if (r.lot_id) line.lot_id = r.lot_id;
    if (dims.__location) line.location_id = dims.__location;
    setLines((ls) => [...ls, line]);
  }

  function addLine() {
    if (!itemId) return;
    setLines((ls) => [...ls, { item_id: itemId, quantity: qty, location_id: dims.__location || null }]);
    setItemId("");
  }

  async function create() {
    if (!concept) return;
    const dimensions = Object.fromEntries(
      requiredDimensions(concept).map((k) => [SLOT[k], dims[k] ?? ""]).filter(([, v]) => v),
    );
    const input = { concept_id: concept.id, warehouse_id: warehouseId, reason: reason || null, dimensions, lines };
    const problems = validateDraft(concept, input);
    if (problems.length > 0) {
      setMessage(problems.map((p) => p.message).join(" · "));
      return;
    }
    try {
      const doc = await api.create(input);
      setMessage(`Salida ${doc.number} creada en borrador`);
      setLines([]);
      setReason("");
      await reload();
    } catch (e) {
      setMessage(errorText(e));
    }
  }

  async function act(doc: IssueDto, action: IssueAction) {
    try {
      if (action === "simulate") {
        setSim(await api.simulate(doc.id));
        return;
      }
      let body: { reason?: string; parameters?: Record<string, unknown> } = {};
      if (action === "reverse") {
        const r = window.prompt("Motivo de la reversión");
        if (!r) return;
        body = { reason: r };
      }
      if (action === "approve") body = { parameters: { decision: "approve" } };
      const out = await api.action(doc.id, action, body);
      setMessage(`Salida ${out.number}: ${STATUS_LABEL[out.status]}`);
      setSim(null);
      await reload();
    } catch (e) {
      setMessage(errorText(e));
    }
  }

  return (
    <section className="nx-card flex flex-col gap-4" aria-labelledby="inv-issue-title">
      <h2 id="inv-issue-title" className="text-base font-semibold text-fg">Salidas de inventario por concepto</h2>

      <fieldset className="grid gap-2 md:grid-cols-3">
        <legend className="text-sm font-semibold">Nueva salida</legend>
        <label className="text-sm" htmlFor="iss-concept">Concepto
          <select id="iss-concept" className="nx-input" value={conceptId} onChange={(e) => setConceptId(e.target.value)}>
            <option value="">—</option>
            {concepts.map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
          </select>
        </label>
        <label className="text-sm" htmlFor="iss-wh">Bodega
          <select id="iss-wh" className="nx-input" value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)}>
            <option value="">—</option>
            {warehouses.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}
          </select>
        </label>
        {concept?.requires_reason && (
          <label className="text-sm" htmlFor="iss-reason">Motivo{concept.reason_codes.length > 0 ? ` (${concept.reason_codes.join(", ")})` : ""}
            <input id="iss-reason" className="nx-input" value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        )}
        {concept && requiredDimensions(concept).map((k) => (
          <label key={k} className="text-sm" htmlFor={`iss-dim-${k}`}>{DIMENSION_LABEL[k]}
            <input id={`iss-dim-${k}`} className="nx-input font-mono" value={dims[k] ?? ""} onChange={(e) => setDims((d) => ({ ...d, [k]: e.target.value.trim() }))} />
          </label>
        ))}
      </fieldset>

      <div className="grid gap-2 md:grid-cols-2">
        <ScanInput api={barcodes} onResolved={onScan} id="iss-scan" label="Escanear producto, presentación, lote o ubicación" />
        <div className="flex items-end gap-1">
          <label className="text-sm" htmlFor="iss-item">Id del producto
            <input id="iss-item" className="nx-input font-mono" value={itemId} onChange={(e) => setItemId(e.target.value.trim())} />
          </label>
          <label className="text-sm" htmlFor="iss-qty">Cantidad
            <input id="iss-qty" className="nx-input w-24" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value.trim())} />
          </label>
          <button type="button" className="nx-btn" onClick={addLine}>Agregar línea</button>
        </div>
      </div>

      {lines.length > 0 && (
        <ol className="text-sm" aria-label="Líneas de la salida">
          {lines.map((l, i) => (
            <li key={i}>
              {l.item_id} × {l.quantity ?? `${l.qty_packages ?? ""} empaques`}
              {l.lot_id ? ` · lote ${l.lot_id}` : ""}
              <button type="button" className="nx-link ml-2" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}>Quitar</button>
            </li>
          ))}
        </ol>
      )}
      <div>
        <button type="button" className="nx-btn nx-btn-primary" disabled={!concept || !warehouseId} onClick={() => void create()}>Crear borrador</button>
      </div>

      {message && <p role="status" className="text-sm">{message}</p>}

      {sim?.data && (
        <table className="nx-table text-sm" aria-label="Vista previa contable">
          <thead><tr><th>Cuenta</th><th>Débito</th><th>Crédito</th></tr></thead>
          <tbody>
            {sim.data.journal.map((j, i) => (
              <tr key={i}><td>{j.account_code} {j.account_name}</td><td>{formatCop(j.debit)}</td><td>{formatCop(j.credit)}</td></tr>
            ))}
          </tbody>
        </table>
      )}

      <table className="nx-table text-sm" aria-label="Salidas">
        <thead><tr><th>Número</th><th>Fecha</th><th>Concepto</th><th>Estado</th><th>Valor</th><th>Acciones</th></tr></thead>
        <tbody>
          {issues.map((d) => (
            <tr key={d.id}>
              <td>{d.number}</td>
              <td>{d.document_date}</td>
              <td>{d.concept_code}</td>
              <td>{STATUS_LABEL[d.status]}</td>
              <td>{d.total ? formatCop(d.total.amount) : "—"}</td>
              <td className="flex gap-1">
                {issueActions(d.status).map((a) => (
                  <button key={a} type="button" className="nx-btn" onClick={() => void act(d, a)}>{ACTION_LABEL[a]}</button>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
