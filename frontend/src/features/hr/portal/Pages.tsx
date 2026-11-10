/**
 * Employee portal pages (F-PORTAL-EMP, owner P13): Desprendibles, Certificados, Vacaciones, Actualización de datos,
 * Perfil. Rendered inside the P08 portal shell (portal.descriptor.ts). Every call goes to `/api/portal/v1/employee/*`;
 * the server scopes everything by the token's employee (the UI is not a security boundary).
 */
import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState } from "@ds/components/Feedback";
import { ApiError } from "@lib/api/client";
import { usePortal } from "@shell/portal/PortalShell";
import { employeePortalApi } from "./api";
import {
  CERTIFICATE_LABEL,
  FIXTURE_NOTICE,
  UPDATABLE_FIELDS,
  VACATION_LABEL,
  dataUpdateChanges,
  formatCop,
  kpiById,
  validateVacation,
  vacationTone,
  type EmployeeCertificate,
  type EmployeeDataUpdate,
  type EmployeePayslip,
  type EmployeeProfile,
  type EmployeeVacation,
  type PortalKpi,
} from "./model";

type Load<T> = { data: T | null; error: string | null; loading: boolean; reload: () => void };

function useLoad<T>(fn: (signal: AbortSignal) => Promise<T>): Load<T> {
  const [tick, setTick] = useState(0);
  const [s, setS] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  useEffect(() => {
    const c = new AbortController();
    setS((p) => ({ ...p, loading: true }));
    fn(c.signal)
      .then((data) => !c.signal.aborted && setS({ data, error: null, loading: false }))
      .catch((e: unknown) => !c.signal.aborted && setS({ data: null, error: e instanceof Error ? e.message : String(e), loading: false }));
    return () => c.abort();
  }, [tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...s, reload };
}

function useEmployeeApi() {
  const { api } = usePortal();
  return useMemo(() => employeePortalApi(api), [api]);
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-labelledby="ep-title" className="flex flex-col gap-4 p-6">
      <h1 id="ep-title" className="text-lg font-semibold">
        {title}
      </h1>
      {children}
    </section>
  );
}

function Status<T>({ load, empty, children }: { load: Load<T>; empty: boolean; children: ReactNode }) {
  if (load.loading && !load.data) return <p className="text-sm text-muted">Cargando…</p>;
  if (load.error) return <p role="alert" className="text-sm text-danger">No se pudo cargar la información.</p>;
  if (empty) return <EmptyState title="Sin documentos" body="Aún no hay información disponible." />;
  return <>{children}</>;
}

export function PayslipsPage() {
  const api = useEmployeeApi();
  const load = useLoad((signal) => api.payslips(signal));
  const items: EmployeePayslip[] = load.data?.items ?? [];
  return (
    <Section title="Desprendibles de pago">
      <Status load={load} empty={items.length === 0}>
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Mis desprendibles de pago contabilizados</caption>
          <thead>
            <tr>
              <th scope="col">Periodo</th>
              <th scope="col">Neto pagado</th>
              <th scope="col">Descarga</th>
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id} data-payslip={p.id}>
                <td>{p.period}</td>
                <td>{formatCop(p.net_pay)}</td>
                <td>
                  <a href={api.payslipPdfUrl(p.id)} download>
                    PDF del periodo {p.period}
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Status>
    </Section>
  );
}

export function CertificatesPage() {
  const api = useEmployeeApi();
  const load = useLoad((signal) => api.certificates(signal));
  const items: EmployeeCertificate[] = load.data?.items ?? [];
  return (
    <Section title="Certificados">
      <p role="note" className="text-xs text-warning" data-legal-label="certificates">
        <Badge tone="warning">{FIXTURE_NOTICE}</Badge>
      </p>
      <Status load={load} empty={items.length === 0}>
        <ul className="flex flex-col gap-2 text-sm">
          {items.map((c) => (
            <li key={c.id} data-certificate={c.id} className="flex items-center justify-between gap-4">
              <span>
                {CERTIFICATE_LABEL[c.certificate_type]} {c.year}
              </span>
              <a href={api.certificatePdfUrl(c.id)} download>
                Descargar PDF
              </a>
            </li>
          ))}
        </ul>
      </Status>
    </Section>
  );
}

export function VacationsPage() {
  const api = useEmployeeApi();
  const load = useLoad((signal) => Promise.all([api.vacations(signal), api.kpis(signal)]));
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const items: EmployeeVacation[] = load.data?.[0].items ?? [];
  const kpis: PortalKpi[] = load.data?.[1].kpis ?? [];
  const balance = kpiById(kpis, "ep_dias_vacaciones");
  const available = balance?.state === "ok" ? balance.value : null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const problem = validateVacation(start, end, available);
    if (problem) return setMsg({ tone: "error", text: problem });
    try {
      await api.requestVacation(start, end);
      setMsg({ tone: "ok", text: "Solicitud enviada para aprobación." });
      setStart("");
      setEnd("");
      load.reload();
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "";
      setMsg({ tone: "error", text: code === "vacation_balance_insufficient" ? "Saldo de vacaciones insuficiente." : "No se pudo enviar la solicitud." });
    }
  }

  return (
    <Section title="Vacaciones">
      <p data-kpi="ep_dias_vacaciones" className="text-sm">
        Días disponibles: <strong>{available ?? "—"}</strong>
        {balance && balance.state !== "ok" && <span className="ml-2 text-muted">(no disponible)</span>}
      </p>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3" aria-label="Solicitar vacaciones">
        <label className="flex flex-col text-sm">
          Desde
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} required />
        </label>
        <label className="flex flex-col text-sm">
          Hasta
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} required />
        </label>
        <Button type="submit">Solicitar</Button>
      </form>
      {msg && (
        <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "text-sm text-danger" : "text-sm text-positive"}>
          {msg.text}
        </p>
      )}
      <Status load={load} empty={items.length === 0}>
        <ul className="flex flex-col gap-2 text-sm">
          {items.map((v) => (
            <li key={v.id} data-vacation={v.id} className="flex items-center gap-3">
              <span>
                {v.start_date} – {v.end_date} ({v.days} días hábiles)
              </span>
              <Badge tone={vacationTone(v.request_status)}>{VACATION_LABEL[v.request_status]}</Badge>
            </li>
          ))}
        </ul>
      </Status>
    </Section>
  );
}

export function DataUpdatesPage() {
  const api = useEmployeeApi();
  const load = useLoad((signal) => Promise.all([api.dataUpdates(signal), api.profile(signal)]));
  const [form, setForm] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const items: EmployeeDataUpdate[] = load.data?.[0].items ?? [];
  const profile = load.data?.[1];

  async function submit(e: FormEvent) {
    e.preventDefault();
    const changes = dataUpdateChanges(form, { email: profile?.email ?? null });
    if (Object.keys(changes).length === 0) return setMsg("No hay cambios para enviar.");
    try {
      await api.requestDataUpdate(changes);
      setMsg("Solicitud enviada a Talento humano.");
      setForm({});
      load.reload();
    } catch {
      setMsg("No se pudo enviar la solicitud.");
    }
  }

  return (
    <Section title="Actualización de datos">
      <form onSubmit={submit} className="flex flex-col gap-3" aria-label="Solicitar actualización de datos">
        {UPDATABLE_FIELDS.map((f) => (
          <label key={f.id} className="flex flex-col text-sm">
            {f.label}
            <input value={form[f.id] ?? ""} onChange={(e) => setForm({ ...form, [f.id]: e.target.value })} />
          </label>
        ))}
        <Button type="submit">Enviar a revisión</Button>
      </form>
      {msg && <p role="status" className="text-sm">{msg}</p>}
      <Status load={load} empty={items.length === 0}>
        <ul className="flex flex-col gap-2 text-sm">
          {items.map((r) => (
            <li key={r.id} data-update={r.id}>
              {Object.keys(r.changes).join(", ")} — {r.review_status === "SUBMITTED" ? "En revisión" : r.review_status === "APPROVED" ? "Aprobada" : "Rechazada"}
            </li>
          ))}
        </ul>
      </Status>
    </Section>
  );
}

export function ProfilePage() {
  const api = useEmployeeApi();
  const load = useLoad((signal) => api.profile(signal));
  const p: EmployeeProfile | null = load.data;
  return (
    <Section title="Perfil">
      <Status load={load} empty={!p}>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          <dt>Nombre</dt>
          <dd>{p?.full_name}</dd>
          <dt>Correo</dt>
          <dd>{p?.email ?? "—"}</dd>
          <dt>Cargo</dt>
          <dd>{p?.position ?? "—"}</dd>
        </dl>
      </Status>
    </Section>
  );
}
