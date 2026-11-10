/**
 * Supplier portal pages (F-PORTAL-SUP, owner P11 part P11b): Cotizaciones, Órdenes, Envíos, Facturas, Pagos, Perfil y
 * documentos. Rendered inside the P08 portal shell (portal.descriptor.ts); "Inicio" is the shell's KPI dashboard.
 * Every call goes to `/api/portal/v1/supplier/*`; the server scopes everything by the token's party (the UI is not a
 * security boundary). Success messages are shown only after the API confirms.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState } from "@ds/components/Feedback";
import { ApiError } from "@lib/api/client";
import { usePortal } from "@shell/portal/PortalShell";
import { supplierPortalApi, type SupplierOrder, type SupplierRfq } from "./api";
import { FIXTURE_NOTICE, ORDER_STATUS_LABEL, PAYMENT_STATUS_LABEL, approvalsNeeded, canAnswer, canQuote, quoteLines, stripDataUrl } from "./model";

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

function useSupplierApi() {
  const { api } = usePortal();
  return useMemo(() => supplierPortalApi(api), [api]);
}

function problem(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "portal_rfq_closed") return "La solicitud de cotización ya cerró.";
    if (e.code === "portal_profile_change_pending") return "Ya tiene un cambio de perfil pendiente de aprobación.";
    return e.message || "La operación no fue aceptada.";
  }
  return "La operación no fue aceptada.";
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-labelledby="sp-title" className="flex flex-col gap-4 p-6">
      <h1 id="sp-title" className="text-lg font-semibold">
        {title}
      </h1>
      {children}
      <p className="text-xs text-muted">{FIXTURE_NOTICE}</p>
    </section>
  );
}

function Status<T>({ load, empty, children }: { load: Load<T>; empty: boolean; children: ReactNode }) {
  if (load.loading && !load.data) return <p className="text-sm text-muted">Cargando…</p>;
  if (load.error) return <p role="alert" className="text-sm text-danger">No se pudo cargar la información.</p>;
  if (empty) return <EmptyState title="Sin información" body="Aún no hay movimientos para su cuenta." />;
  return <>{children}</>;
}

function Notice({ msg }: { msg: { ok: boolean; text: string } | null }) {
  if (!msg) return null;
  return (
    <p role={msg.ok ? "status" : "alert"} className={msg.ok ? "text-sm" : "text-sm text-danger"}>
      {msg.text}
    </p>
  );
}

function QuoteForm({ rfq, onDone }: { rfq: SupplierRfq; onDone: (text: string) => void }) {
  const api = useSupplierApi();
  const [prices, setPrices] = useState<Record<number, string>>({});
  const [lead, setLead] = useState("5");
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  async function send() {
    const { lines, errors: errs } = quoteLines(rfq, prices);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    try {
      const q = await api.submitQuote(rfq.id, { currency: rfq.currency, lead_time_days: Number(lead) || 0, lines });
      onDone(`Cotización ${q.number} enviada.`);
    } catch (e) {
      setMsg({ ok: false, text: problem(e) });
    }
  }
  return (
    <form aria-label={`Cotizar ${rfq.number}`} className="nx-card flex flex-col gap-3 p-4" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      {rfq.lines.map((ln) => (
        <label key={ln.line_no} className="flex flex-col gap-1 text-sm">
          {`${ln.line_no}. ${ln.description} — ${ln.quantity.value} ${ln.quantity.uom} (precio unitario ${rfq.currency})`}
          <input className="nx-input" inputMode="decimal" value={prices[ln.line_no] ?? ""} aria-invalid={Boolean(errors[ln.line_no]) || undefined} onChange={(e) => setPrices({ ...prices, [ln.line_no]: e.target.value })} />
          {errors[ln.line_no] ? <span role="alert" className="text-danger">{errors[ln.line_no]}</span> : null}
        </label>
      ))}
      <label className="flex flex-col gap-1 text-sm">
        Tiempo de entrega (días)
        <input className="nx-input" inputMode="numeric" value={lead} onChange={(e) => setLead(e.target.value)} />
      </label>
      <Button variant="primary" type="submit">Enviar cotización</Button>
      <Notice msg={msg} />
    </form>
  );
}

export function RfqsPage() {
  const api = useSupplierApi();
  const load = useLoad((signal) => api.rfqs(signal));
  const [open, setOpen] = useState<SupplierRfq | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const items = load.data?.items ?? [];
  return (
    <Section title="Solicitudes de cotización">
      <Notice msg={msg} />
      <Status load={load} empty={items.length === 0}>
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Solicitudes de cotización a las que fue invitado</caption>
          <thead>
            <tr>
              <th scope="col">Número</th>
              <th scope="col">Cierre</th>
              <th scope="col">Estado</th>
              <th scope="col"><span className="sr-only">Acción</span></th>
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id}>
                <td>{r.number}</td>
                <td>{new Date(r.deadline).toLocaleString("es-CO", { timeZone: "America/Bogota" })}</td>
                <td><Badge>{r.rfq_status === "OPEN" ? "Abierta" : "Cerrada"}</Badge></td>
                <td>{canQuote(r) ? <Button onClick={() => setOpen(r)}>Cotizar</Button> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Status>
      {open ? <QuoteForm rfq={open} onDone={(text) => { setMsg({ ok: true, text }); setOpen(null); load.reload(); }} /> : null}
    </Section>
  );
}

export function OrdersPage() {
  const api = useSupplierApi();
  const load = useLoad((signal) => api.orders(signal));
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const items: SupplierOrder[] = load.data?.items ?? [];
  async function answer(o: SupplierOrder, accepted: boolean) {
    if (!accepted && !comment.trim()) {
      setMsg({ ok: false, text: "Describa los cambios que solicita." });
      return;
    }
    try {
      const out = await api.answerOrder(o.id, accepted, comment);
      setMsg({ ok: true, text: accepted ? `Orden ${out.number} confirmada.` : `Se enviaron los cambios de la orden ${out.number}.` });
      setComment("");
      load.reload();
    } catch (e) {
      setMsg({ ok: false, text: problem(e) });
    }
  }
  return (
    <Section title="Órdenes de compra">
      <Notice msg={msg} />
      <label className="flex max-w-xl flex-col gap-1 text-sm">
        Comentario (obligatorio para solicitar cambios)
        <input className="nx-input" value={comment} maxLength={2000} onChange={(e) => setComment(e.target.value)} />
      </label>
      <Status load={load} empty={items.length === 0}>
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Mis órdenes de compra</caption>
          <thead>
            <tr>
              <th scope="col">Número</th>
              <th scope="col">Estado</th>
              <th scope="col">Total</th>
              <th scope="col"><span className="sr-only">Acciones</span></th>
            </tr>
          </thead>
          <tbody>
            {items.map((o) => (
              <tr key={o.id}>
                <td>{o.number}</td>
                <td><Badge>{ORDER_STATUS_LABEL[o.status] ?? o.status}</Badge></td>
                <td className="tabular-nums">{`${o.total.amount} ${o.total.currency}`}</td>
                <td className="flex gap-2">
                  {canAnswer(o) ? (
                    <>
                      <Button variant="primary" onClick={() => void answer(o, true)}>Confirmar</Button>
                      <Button variant="ghost" onClick={() => void answer(o, false)}>Solicitar cambios</Button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Status>
    </Section>
  );
}

export function ShipmentsPage() {
  const api = useSupplierApi();
  const load = useLoad((signal) => api.shipments(signal));
  const orders = useLoad((signal) => api.orders(signal));
  const [form, setForm] = useState({ order: "", date: "", tracking: "", line: "1", qty: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  async function announce() {
    try {
      const out = await api.announce({
        purchase_order_id: form.order,
        expected_date: form.date,
        tracking: form.tracking,
        lines: [{ line_no: Number(form.line), quantity: form.qty }],
      });
      setMsg({ ok: true, text: `Aviso de despacho ${out.number} registrado.` });
      load.reload();
    } catch (e) {
      setMsg({ ok: false, text: problem(e) });
    }
  }
  const items = load.data?.items ?? [];
  const shippable = (orders.data?.items ?? []).filter((o) => ["SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"].includes(o.status));
  return (
    <Section title="Avisos de despacho">
      <form aria-label="Nuevo aviso de despacho" className="nx-card grid grid-cols-1 gap-3 p-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void announce(); }}>
        <label className="flex flex-col gap-1 text-sm">
          Orden de compra
          <select className="nx-input" value={form.order} onChange={(e) => setForm({ ...form, order: e.target.value })}>
            <option value="">Seleccione…</option>
            {shippable.map((o) => (
              <option key={o.id} value={o.id}>{o.number}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Fecha estimada de entrega
          <input className="nx-input" type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Línea
          <input className="nx-input" inputMode="numeric" value={form.line} onChange={(e) => setForm({ ...form, line: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Cantidad despachada
          <input className="nx-input" inputMode="decimal" value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          Guía / seguimiento
          <input className="nx-input" value={form.tracking} maxLength={255} onChange={(e) => setForm({ ...form, tracking: e.target.value })} />
        </label>
        <div><Button variant="primary" type="submit" disabled={!form.order || !form.date || !form.qty}>Registrar aviso</Button></div>
        <Notice msg={msg} />
      </form>
      <Status load={load} empty={items.length === 0}>
        <ul className="text-sm">
          {items.map((s) => (
            <li key={s.id}>{`${s.number} — entrega ${s.expected_date} — ${s.tracking || "sin guía"}`}</li>
          ))}
        </ul>
      </Status>
    </Section>
  );
}

export function InvoicesPage() {
  const api = useSupplierApi();
  const load = useLoad((signal) => api.invoices(signal));
  const [number, setNumber] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  async function upload() {
    let xml = "";
    if (file) {
      xml = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(stripDataUrl(String(r.result)));
        r.onerror = () => reject(new Error("read failed"));
        r.readAsDataURL(file);
      });
    }
    try {
      const out = await api.uploadInvoice(number, xml);
      setMsg({ ok: true, text: `Factura ${out.number} recibida para revisión.` });
      load.reload();
    } catch (e) {
      setMsg({ ok: false, text: problem(e) });
    }
  }
  const items = load.data?.items ?? [];
  return (
    <Section title="Facturas">
      <form aria-label="Cargar factura electrónica" className="nx-card flex flex-col gap-3 p-4" onSubmit={(e) => { e.preventDefault(); void upload(); }}>
        <label className="flex flex-col gap-1 text-sm">
          Número de la factura
          <input className="nx-input" value={number} maxLength={60} onChange={(e) => setNumber(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Archivo XML (opcional, máximo 2 MB)
          <input type="file" accept=".xml,application/xml,text/xml" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <Button variant="primary" type="submit" disabled={!number.trim()}>Cargar</Button>
        <Notice msg={msg} />
      </form>
      <Status load={load} empty={items.length === 0}>
        <ul className="text-sm">
          {items.map((i) => (
            <li key={i.id}>{`${i.number} — ${i.processing_status}`}</li>
          ))}
        </ul>
      </Status>
    </Section>
  );
}

export function PaymentsPage() {
  const api = useSupplierApi();
  const load = useLoad((signal) => api.payments(signal));
  const items = load.data?.items ?? [];
  return (
    <Section title="Pagos">
      <Status load={load} empty={items.length === 0}>
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Estado de mis cuentas por cobrar a la compañía</caption>
          <thead>
            <tr>
              <th scope="col">Documento</th>
              <th scope="col">Valor</th>
              <th scope="col">Estado</th>
              <th scope="col">Comprobantes</th>
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id}>
                <td>{p.document.number}</td>
                <td className="tabular-nums">{`${p.amount.amount} ${p.amount.currency}`}</td>
                <td><Badge>{PAYMENT_STATUS_LABEL[p.status] ?? p.status}</Badge></td>
                <td>{p.vouchers.map((v) => `${v.number} (${v.state})`).join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Status>
    </Section>
  );
}

export function ProfilePage() {
  const api = useSupplierApi();
  const load = useLoad((signal) => api.profile(signal));
  const docs = useLoad((signal) => api.documents(signal));
  const [bank, setBank] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  async function request() {
    const parameters = { bank_account: bank };
    try {
      const out = await api.requestChange(parameters, "Actualización desde el portal");
      setMsg({ ok: true, text: `Cambio enviado: requiere ${out.data.required_approvals} aprobaciones internas antes de aplicarse.` });
      setBank("");
      load.reload();
    } catch (e) {
      setMsg({ ok: false, text: problem(e) });
    }
  }
  const p = load.data;
  return (
    <Section title="Perfil y documentos">
      <Status load={load} empty={false}>
        {p ? (
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <dt>Razón social</dt>
            <dd>{p.legal_name}</dd>
            <dt>NIT</dt>
            <dd>{p.nit}</dd>
            <dt>Cuenta bancaria</dt>
            <dd>{p.bank_account ?? "Sin registrar"}</dd>
            <dt>Cambio pendiente</dt>
            <dd>{p.pending_change ? "Sí, en aprobación" : "No"}</dd>
          </dl>
        ) : null}
      </Status>
      <form aria-label="Solicitar cambio de cuenta bancaria" className="nx-card flex flex-col gap-3 p-4" onSubmit={(e) => { e.preventDefault(); void request(); }}>
        <label className="flex flex-col gap-1 text-sm">
          Nueva cuenta bancaria (solo dígitos)
          <input className="nx-input" inputMode="numeric" autoComplete="off" value={bank} onChange={(e) => setBank(e.target.value)} />
        </label>
        <p className="text-xs text-muted">{`Este cambio requiere ${approvalsNeeded({ bank_account: bank })} aprobaciones internas; mientras tanto se mantiene la cuenta actual.`}</p>
        <Button variant="primary" type="submit" disabled={!bank.trim() || Boolean(p?.pending_change)}>Solicitar cambio</Button>
        <Notice msg={msg} />
      </form>
      <Status load={docs} empty={(docs.data?.items ?? []).length === 0}>
        <ul className="text-sm">
          {(docs.data?.items ?? []).map((d) => (
            <li key={d.id}>{`${d.document_type}: ${d.file_name} — ${d.review_status}`}</li>
          ))}
        </ul>
      </Status>
    </Section>
  );
}
