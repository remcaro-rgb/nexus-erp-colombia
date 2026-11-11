// Inventario › Maestros › Conceptos de salida (F-INV-ISSUE): the seeded concepts plus company-defined ones; each maps
// to an account-determination role (never a hard-coded PUC account) and declares dimension/reason/approval rules.
import { useEffect, useMemo, useState } from "react";
import { useShell } from "@shell/ShellContext";
import { ApiError } from "@lib/api/client";
import { IssuesApi, type IssueConceptDto } from "./api";
import { DIMENSION_LABEL, formatCop, requiredDimensions } from "./model";

export default function IssueConceptsPage() {
  const shell = useShell();
  const api = useMemo(() => new IssuesApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
  const [concepts, setConcepts] = useState<IssueConceptDto[]>([]);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("INVENTORY_OTHER_ISSUE_EXPENSE");
  const [requiresReason, setRequiresReason] = useState(false);
  const [requiresApproval, setRequiresApproval] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function reload(signal?: AbortSignal) {
    try {
      setConcepts((await api.concepts(signal)).items);
    } catch (e) {
      if (!signal?.aborted) setMessage(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));
    }
  }

  useEffect(() => {
    const ctl = new AbortController();
    void reload(ctl.signal);
    return () => ctl.abort();
  }, [api]);

  async function create() {
    try {
      const c = await api.createConcept({ code, name, account_role: role, requires_reason: requiresReason, requires_approval: requiresApproval });
      setMessage(`Concepto ${c.code} creado`);
      setCode("");
      setName("");
      await reload();
    } catch (e) {
      setMessage(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));
    }
  }

  return (
    <section className="nx-card flex flex-col gap-4" aria-labelledby="inv-concepts-title">
      <h2 id="inv-concepts-title" className="text-base font-semibold text-fg">Conceptos de salida</h2>
      <table className="nx-table text-sm" aria-label="Conceptos">
        <thead><tr><th>Código</th><th>Nombre</th><th>Rol contable</th><th>Dimensiones</th><th>Motivo</th><th>Aprobación</th><th>Estado</th></tr></thead>
        <tbody>
          {concepts.map((c) => (
            <tr key={c.id}>
              <td>{c.code}</td>
              <td>{c.name}</td>
              <td className="font-mono">{c.account_role}</td>
              <td>{requiredDimensions(c).map((d) => DIMENSION_LABEL[d]).join(", ") || "—"}</td>
              <td>{c.requires_reason ? (c.reason_codes.join(", ") || "Sí") : "No"}</td>
              <td>{c.requires_approval ? (c.approval_threshold_value ? `> ${formatCop(c.approval_threshold_value.amount)}` : "Siempre") : "No"}</td>
              <td>{c.status === "ACTIVE" ? "Activo" : "Archivado"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <fieldset className="flex flex-wrap items-end gap-2">
        <legend className="text-sm font-semibold">Nuevo concepto</legend>
        <label className="text-sm" htmlFor="ic-code">Código<input id="ic-code" className="nx-input" value={code} onChange={(e) => setCode(e.target.value.trim().toUpperCase())} /></label>
        <label className="text-sm" htmlFor="ic-name">Nombre<input id="ic-name" className="nx-input" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="text-sm" htmlFor="ic-role">Rol contable<input id="ic-role" className="nx-input font-mono" value={role} onChange={(e) => setRole(e.target.value.trim())} /></label>
        <label className="text-sm"><input type="checkbox" checked={requiresReason} onChange={(e) => setRequiresReason(e.target.checked)} /> Exige motivo</label>
        <label className="text-sm"><input type="checkbox" checked={requiresApproval} onChange={(e) => setRequiresApproval(e.target.checked)} /> Exige aprobación</label>
        <button type="button" className="nx-btn nx-btn-primary" disabled={!code || !name} onClick={() => void create()}>Crear</button>
      </fieldset>
      {message && <p role="status" className="text-sm">{message}</p>}
    </section>
  );
}
