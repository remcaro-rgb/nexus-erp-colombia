// Compras › Solicitudes internas (F-INV-REQ, owner P11 part P11b): tabs Mis solicitudes · Por aprobar · Consolidación
// · Abastecimiento. Success is shown only after the API confirms; every action is authorized by the server.
import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { ApiError, createApiClient } from "@lib/api/client";
import { can } from "@lib/permissions";
import { useOptionalShell } from "@shell/ShellContext";
import { RequisitionsApi, type ConsolidationRunResult, type RequisitionDto, type SourcingLinePlan } from "./api";
import {
  ACTION_LABEL,
  LINE_STATUS_LABEL,
  REASON_LABEL,
  STATUS_LABEL,
  canDecide,
  lineageBalanced,
  sourcingSummary,
  toRequest,
  validateDraft,
  type DraftForm,
} from "./model";

function today(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Bogota" }).format(new Date());
}

function useRequisitionsApi() {
  const shell = useOptionalShell();
  const client = shell?.api;
  const companyId = shell?.companyId ?? null;
  const api = useMemo(() => new RequisitionsApi(client ?? createApiClient(), companyId), [client, companyId]);
  return { api, companyId, userId: shell?.userId ?? null, can: (code: string) => (shell ? can(shell.perms, code) : false) };
}

function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message || e.code || "La operación no fue aceptada.";
  return "La operación no fue aceptada.";
}

function useList(api: RequisitionsApi, params: Record<string, string> | undefined, key: string) {
  const [rows, setRows] = useState<RequisitionDto[] | null>(null);
  const [error, setError] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const c = new AbortController();
    setRows(null);
    setError(false);
    api.list(params, c.signal).then(
      (p) => !c.signal.aborted && setRows(p.items),
      () => !c.signal.aborted && setError(true),
    );
    return () => c.abort(); // a response for a previous company is discarded
  }, [api, key, tick]);
  return { rows, error, reload: useCallback(() => setTick((t) => t + 1), []) };
}

function RequisitionTable({ rows, actions }: { rows: RequisitionDto[]; actions?: (r: RequisitionDto) => ReactNode }) {
  return (
    <table className="nx-card w-full text-sm" aria-label="Solicitudes internas">
      <thead>
        <tr className="text-left text-xs text-muted">
          <th scope="col">Número</th>
          <th scope="col">Estado</th>
          <th scope="col">Se necesita</th>
          <th scope="col">Prioridad</th>
          <th scope="col">Traslado / Compra / Cumplido</th>
          <th scope="col">
            <span className="sr-only">Acciones</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const s = sourcingSummary(r);
          return (
            <tr key={r.id}>
              <td>{r.number}</td>
              <td>
                <Badge>{STATUS_LABEL[r.requisition_status] ?? r.requisition_status}</Badge>
              </td>
              <td>{r.needed_by}</td>
              <td>{r.priority}</td>
              <td className="tabular-nums">{`${s.transfer} / ${s.purchase} / ${s.fulfilled}`}</td>
              <td className="flex gap-2">{actions?.(r)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Tab "Mis solicitudes": own requisitions + the request form (DRAFT → enviar a aprobación). */
export function MyRequisitionsPage() {
  const { api, companyId, userId } = useRequisitionsApi();
  const list = useList(api, userId ? { requester_id: userId } : undefined, `${companyId}:${userId}`);
  const [form, setForm] = useState<DraftForm>({ warehouseId: "", neededBy: "", priority: "NORMAL", notes: "", lines: [{ itemId: "", qty: "", uom: "UND" }] });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const errs = validateDraft(form, today());
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const created = await api.create(toRequest(form, today()));
      await api.action(created.id, "submit");
      setMsg({ tone: "ok", text: `Se envió la solicitud ${created.number} a aprobación.` });
      list.reload();
    } catch (err) {
      setMsg({ tone: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }
  const line = form.lines[0] ?? { itemId: "", qty: "", uom: "UND" };
  return (
    <div className="flex flex-col gap-6" data-nx-page="compras.solicitudes.mias">
      <form onSubmit={(e) => void submit(e)} className="nx-card grid max-w-3xl grid-cols-1 gap-4 p-4 sm:grid-cols-2" aria-label="Nueva solicitud interna">
        <label className="flex flex-col gap-1 text-sm">
          Bodega que solicita (id)
          <input className="nx-input" value={form.warehouseId} aria-invalid={Boolean(errors.warehouseId) || undefined} onChange={(e) => setForm({ ...form, warehouseId: e.target.value })} />
          {errors.warehouseId ? <span role="alert" className="text-danger">{errors.warehouseId}</span> : null}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Se necesita el
          <input className="nx-input" type="date" value={form.neededBy} aria-invalid={Boolean(errors.neededBy) || undefined} onChange={(e) => setForm({ ...form, neededBy: e.target.value })} />
          {errors.neededBy ? <span role="alert" className="text-danger">{errors.neededBy}</span> : null}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Producto (id)
          <input className="nx-input" value={line.itemId} onChange={(e) => setForm({ ...form, lines: [{ ...line, itemId: e.target.value }] })} />
          {errors["lines.0.itemId"] ? <span role="alert" className="text-danger">{errors["lines.0.itemId"]}</span> : null}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Cantidad (unidad base)
          <input className="nx-input" inputMode="decimal" value={line.qty} onChange={(e) => setForm({ ...form, lines: [{ ...line, qty: e.target.value }] })} />
          {errors["lines.0.qty"] ? <span role="alert" className="text-danger">{errors["lines.0.qty"]}</span> : null}
        </label>
        <p className="text-xs text-muted sm:col-span-2">
          Al aprobarse, el sistema reserva lo disponible en otras bodegas de la compañía (traslado en borrador) y envía el resto a compras.
        </p>
        <div className="sm:col-span-2">
          <Button variant="primary" type="submit" loading={busy}>
            Enviar a aprobación
          </Button>
        </div>
        {msg ? (
          <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "text-danger sm:col-span-2" : "sm:col-span-2"}>
            {msg.text}
          </p>
        ) : null}
      </form>
      {list.error ? <EmptyState title="No fue posible cargar sus solicitudes" /> : null}
      {!list.error && !list.rows ? <Skeleton className="h-32 w-full" label="Cargando" /> : null}
      {list.rows && !list.rows.length ? <EmptyState title="Aún no tiene solicitudes internas" /> : null}
      {list.rows?.length ? <RequisitionTable rows={list.rows} /> : null}
    </div>
  );
}

/** Tab "Por aprobar": SUBMITTED requisitions of others (SoD: the own ones cannot be decided). */
export function ApprovalInboxPage() {
  const { api, companyId, userId } = useRequisitionsApi();
  const list = useList(api, { status: "SUBMITTED" }, `${companyId}:inbox`);
  const [msg, setMsg] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  async function decide(r: RequisitionDto, verb: "approve" | "reject") {
    if (verb === "reject" && !reason.trim()) {
      setMsg("Escriba el motivo del rechazo.");
      return;
    }
    try {
      const out = await api.action(r.id, verb, reason);
      setMsg(verb === "approve" ? `Solicitud ${out.number} aprobada y abastecida.` : `Solicitud ${out.number} rechazada.`);
      list.reload();
    } catch (err) {
      setMsg(errorText(err));
    }
  }
  if (list.error) return <EmptyState title="No fue posible cargar la bandeja de aprobación" />;
  if (!list.rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!list.rows.length) return <EmptyState title="No hay solicitudes por aprobar" />;
  return (
    <div className="flex flex-col gap-3" data-nx-page="compras.solicitudes.por-aprobar">
      {msg ? <p role="status">{msg}</p> : null}
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Motivo (obligatorio para rechazar)
        <input className="nx-input" value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} />
      </label>
      <RequisitionTable
        rows={list.rows}
        actions={(r) =>
          canDecide(r, userId) ? (
            <>
              <Button variant="primary" onClick={() => void decide(r, "approve")}>Aprobar</Button>
              <Button variant="ghost" onClick={() => void decide(r, "reject")}>Rechazar</Button>
            </>
          ) : (
            <span className="text-xs text-muted">Solicitud propia (segregación de funciones)</span>
          )
        }
      />
    </div>
  );
}

/** Tab "Consolidación": D-031 preview with lineage (dry run), then the run (D-011 drafts or buyer tasks). */
export function ConsolidationPage() {
  const { api } = useRequisitionsApi();
  const [result, setResult] = useState<ConsolidationRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(dryRun: boolean) {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.consolidate(dryRun));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-col gap-4" data-nx-page="compras.solicitudes.consolidacion">
      <div className="flex gap-2">
        <Button onClick={() => void run(true)} loading={busy}>Vista previa</Button>
        <Button variant="primary" onClick={() => void run(false)} loading={busy} disabled={!result?.dry_run || !result.lines.length}>
          Consolidar y crear borradores
        </Button>
      </div>
      {error ? <p role="alert" className="text-danger">{error}</p> : null}
      {result && !result.lines.length ? <EmptyState title="No hay remanentes por comprar" /> : null}
      {result?.warnings.map((w) => (
        <p key={w.code} role="status" className="text-sm">{REASON_LABEL[w.code] ?? w.message}</p>
      ))}
      {result?.lines.length ? (
        <table className="nx-card w-full text-sm" aria-label={result.dry_run ? "Vista previa de consolidación" : "Consolidación aplicada"}>
          <thead>
            <tr className="text-left text-xs text-muted">
              <th scope="col">Producto</th>
              <th scope="col">Semana</th>
              <th scope="col">Cantidad</th>
              <th scope="col">Origen (solicitud · línea · cantidad)</th>
              <th scope="col">Acción</th>
            </tr>
          </thead>
          <tbody>
            {result.lines.map((ln) => (
              <tr key={`${ln.item_id}-${ln.needed_week}-${ln.delivery_warehouse_id}`}>
                <td>{ln.item_id.slice(0, 8)}</td>
                <td>{ln.needed_week}</td>
                <td className="tabular-nums">{ln.quantity}{lineageBalanced(ln) ? "" : " ⚠"}</td>
                <td>{ln.sources.map((s) => `${s.requisition_number}·${s.line_no}·${s.qty}`).join(", ")}</td>
                <td>{ACTION_LABEL[ln.action]}{ln.reason_code ? ` — ${REASON_LABEL[ln.reason_code] ?? ln.reason_code}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

/** Tab "Abastecimiento": simulate the transfer vs purchase split of an approved requisition, then apply it. */
export function SourcingPage() {
  const { api, companyId } = useRequisitionsApi();
  const list = useList(api, { status: "APPROVED" }, `${companyId}:sourcing`);
  const [plan, setPlan] = useState<{ req: RequisitionDto; lines: SourcingLinePlan[] } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  async function simulate(r: RequisitionDto) {
    try {
      const out = await api.source(r.id, "simulate");
      setPlan({ req: r, lines: out.data.lines as SourcingLinePlan[] });
    } catch (err) {
      setMsg(errorText(err));
    }
  }
  async function apply(r: RequisitionDto) {
    try {
      await api.source(r.id, "apply");
      setMsg(`Abastecimiento aplicado a ${r.number}.`);
      setPlan(null);
      list.reload();
    } catch (err) {
      setMsg(errorText(err));
    }
  }
  return (
    <div className="flex flex-col gap-4" data-nx-page="compras.solicitudes.abastecimiento">
      {msg ? <p role="status">{msg}</p> : null}
      {list.error ? <EmptyState title="No fue posible cargar las solicitudes" /> : null}
      {list.rows && !list.rows.length ? <EmptyState title="No hay solicitudes aprobadas pendientes de abastecer" /> : null}
      {list.rows?.length ? (
        <RequisitionTable rows={list.rows} actions={(r) => <Button onClick={() => void simulate(r)}>Simular</Button>} />
      ) : null}
      {plan ? (
        <section aria-label={`Simulación ${plan.req.number}`} className="nx-card p-4">
          <ul className="text-sm">
            {plan.lines.map((ln) => (
              <li key={ln.line_no}>
                {`Línea ${ln.line_no}: traslado ${ln.qty_transfer} (${ln.transfer.map((t) => `${t.from_warehouse_code} ${t.qty}`).join(", ") || "—"}) · compra ${ln.qty_purchase}`}
              </li>
            ))}
          </ul>
          <Button variant="primary" onClick={() => void apply(plan.req)}>Aplicar</Button>
        </section>
      ) : null}
      <p className="text-xs text-muted">{`Estados de línea: ${Object.values(LINE_STATUS_LABEL).join(" · ")}`}</p>
    </div>
  );
}
