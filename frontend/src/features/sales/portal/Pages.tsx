/**
 * Customer portal pages (F-PORTAL-CUS, owner P10 part P10b): Cotizaciones, Pedidos, Facturas y notas, Pagos y recibos,
 * Estado de cuenta, Solicitudes, Perfil. Rendered inside the P08 portal shell (portal.descriptor.ts); "Inicio" is the
 * shell's KPI dashboard (`cp_*`). Every call goes to `/api/portal/v1/customer/*`; the server scopes everything by the
 * token's party (the UI is not a security boundary). Success messages are shown only after the API confirms.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState } from "@ds/components/Feedback";
import { ApiError } from "@lib/api/client";
import { usePortal } from "@shell/portal/PortalShell";
import { customerPortalApi, type CustomerDocument, type CustomerOpenItem, type RequestType } from "./api";
import {
  DOC_STATUS_LABEL,
  FIXTURE_NOTICE,
  INTENT_STATUS_LABEL,
  PAYMENT_NOTICE,
  PROFILE_FIELDS,
  QUOTE_STATUS_LABEL,
  REQUEST_STATUS_LABEL,
  REQUEST_TYPE_LABEL,
  canAccept,
  canSimulate,
  formatMoney,
  paymentIntent,
  requestBody,
} from "./model";

type Load<T> = { data: T | null; error: string | null; loading: boolean; reload: () => void };
type Msg = { ok: boolean; text: string } | null;

function useLoad<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[] = []): Load<T> {
  const [tick, setTick] = useState(0);
  const [s, setS] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  useEffect(() => {
    const c = new AbortController();
    setS((p) => ({ ...p, loading: true }));
    fn(c.signal)
      .then((data) => !c.signal.aborted && setS({ data, error: null, loading: false }))
      .catch((e: unknown) => !c.signal.aborted && setS({ data: null, error: e instanceof Error ? e.message : String(e), loading: false }));
    return () => c.abort();
  }, [tick, ...deps]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...s, reload };
}

function useCustomerApi() {
  const { api } = usePortal();
  return useMemo(() => customerPortalApi(api), [api]);
}

function problem(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "payment_intent_amount_invalid") return "El monto no es válido para las facturas seleccionadas.";
    if (e.code === "portal_document_not_owned") return "El documento no pertenece a su cuenta.";
    if (e.code === "invalid_state_transition") return "La acción ya no es posible en el estado actual del documento.";
    return e.message || "La operación no fue aceptada.";
  }
  return "La operación no fue aceptada.";
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-labelledby="cp-title" className="flex flex-col gap-4 p-6">
      <h1 id="cp-title" className="text-lg font-semibold">
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
  if (empty) return <EmptyState title="Sin información" body="Aún no hay movimientos para su cuenta en este periodo." />;
  return <>{children}</>;
}

function Notice({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return (
    <p role={msg.ok ? "status" : "alert"} className={msg.ok ? "text-sm" : "text-sm text-danger"}>
      {msg.text}
    </p>
  );
}

function DocTable({ caption, rows, extra }: { caption: string; rows: CustomerDocument[]; extra?: (d: CustomerDocument) => ReactNode }) {
  return (
    <table className="w-full text-left text-sm">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Número</th>
          <th scope="col">Estado</th>
          <th scope="col">Fecha límite</th>
          <th scope="col" className="text-right">Total</th>
          {extra ? <th scope="col"><span className="sr-only">Acciones</span></th> : null}
        </tr>
      </thead>
      <tbody>
        {rows.map((d) => (
          <tr key={d.id}>
            <td>{d.number}</td>
            <td><Badge>{QUOTE_STATUS_LABEL[d.quote_status] ?? DOC_STATUS_LABEL[d.status] ?? d.status}</Badge></td>
            <td>{d.valid_until ?? "—"}</td>
            <td className="text-right tabular-nums">{formatMoney(d.total)}</td>
            {extra ? <td className="flex gap-2">{extra(d)}</td> : null}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function QuotesPage() {
  const api = useCustomerApi();
  const load = useLoad((signal) => api.quotes(signal));
  const [msg, setMsg] = useState<Msg>(null);
  const rows = load.data?.items ?? [];
  async function accept(d: CustomerDocument) {
    try {
      const out = await api.acceptQuote(d.id);
      setMsg({ ok: true, text: `Cotización ${d.number} aceptada. Pedido ${out.resource.number} creado; el equipo comercial lo confirmará.` });
      load.reload();
    } catch (e) {
      setMsg({ ok: false, text: problem(e) });
    }
  }
  return (
    <Section title="Cotizaciones">
      <Notice msg={msg} />
      <Status load={load} empty={rows.length === 0}>
        <DocTable caption="Mis cotizaciones" rows={rows} extra={(d) => (canAccept(d.quote_status) ? <Button variant="primary" onClick={() => void accept(d)}>Aceptar</Button> : null)} />
      </Status>
    </Section>
  );
}

export function OrdersPage() {
  const api = useCustomerApi();
  const load = useLoad((signal) => api.orders(signal));
  const rows = load.data?.items ?? [];
  return (
    <Section title="Pedidos">
      <Status load={load} empty={rows.length === 0}>
        <DocTable caption="Mis pedidos" rows={rows} />
      </Status>
    </Section>
  );
}

export function InvoicesPage() {
  const api = useCustomerApi();
  const invoices = useLoad((signal) => api.invoices(signal));
  const notes = useLoad((signal) => api.creditNotes(signal));
  const inv = invoices.data?.items ?? [];
  const cn = notes.data?.items ?? [];
  return (
    <Section title="Facturas y notas">
      <h2 className="text-base font-semibold">Facturas</h2>
      <Status load={invoices} empty={inv.length === 0}>
        <DocTable
          caption="Mis facturas"
          rows={inv}
          extra={(d) => (
            <>
              <a href={api.invoicePdfUrl(d.id)} download>PDF {d.number}</a>
              <a href={api.invoiceXmlUrl(d.id)} download>XML {d.number}</a>
            </>
          )}
        />
      </Status>
      <h2 className="text-base font-semibold">Notas crédito</h2>
      <Status load={notes} empty={cn.length === 0}>
        <DocTable
          caption="Mis notas crédito"
          rows={cn}
          extra={(d) => (
            <>
              <a href={api.notePdfUrl(d.id)} download>PDF {d.number}</a>
              <a href={api.noteXmlUrl(d.id)} download>XML {d.number}</a>
            </>
          )}
        />
      </Status>
    </Section>
  );
}

export function PaymentsPage() {
  const api = useCustomerApi();
  const open = useLoad((signal) => api.openItems(signal));
  const intents = useLoad((signal) => api.intents(signal));
  const receipts = useLoad((signal) => api.receipts(signal));
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [amount, setAmount] = useState("");
  const [msg, setMsg] = useState<Msg>(null);
  const items: CustomerOpenItem[] = open.data?.items ?? [];
  async function pay() {
    const { body, error } = paymentIntent(items.filter((i) => selected[i.document.id]), amount);
    if (!body) {
      setMsg({ ok: false, text: error ?? "Revise los datos." });
      return;
    }
    try {
      const pi = await api.createIntent(body.documents, body.amount);
      const done = await api.simulate(pi.id, "success");
      setMsg({ ok: true, text: `Intención de pago registrada: ${INTENT_STATUS_LABEL[done.intent_status]}.` });
      setSelected({});
      setAmount("");
      intents.reload();
    } catch (e) {
      setMsg({ ok: false, text: problem(e) });
    }
  }
  return (
    <Section title="Pagos y recibos">
      <p className="text-sm">{PAYMENT_NOTICE}</p>
      <Notice msg={msg} />
      <form aria-label="Pagar facturas" className="nx-card flex flex-col gap-3 p-4" onSubmit={(e) => { e.preventDefault(); void pay(); }}>
        <Status load={open} empty={items.length === 0}>
          <fieldset className="flex flex-col gap-1 text-sm">
            <legend className="font-medium">Facturas pendientes</legend>
            {items.map((i) => (
              <label key={i.document.id} className="flex gap-2">
                <input type="checkbox" checked={Boolean(selected[i.document.id])} onChange={(e) => setSelected({ ...selected, [i.document.id]: e.target.checked })} />
                {`${i.document.number} — vence ${i.due_date ?? "—"} — saldo ${formatMoney(i.balance)}`}
              </label>
            ))}
          </fieldset>
        </Status>
        <label className="flex max-w-xs flex-col gap-1 text-sm">
          Monto a pagar (COP)
          <input className="nx-input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
        <div><Button variant="primary" type="submit" disabled={items.length === 0}>Pagar (simulado)</Button></div>
      </form>
      <h2 className="text-base font-semibold">Mis intenciones de pago</h2>
      <Status load={intents} empty={(intents.data?.items ?? []).length === 0}>
        <ul className="flex flex-col gap-1 text-sm">
          {(intents.data?.items ?? []).map((p) => (
            <li key={p.id} className="flex gap-2">
              <span className="tabular-nums">{formatMoney(p.amount)}</span>
              <Badge>{INTENT_STATUS_LABEL[p.intent_status]}</Badge>
              {canSimulate(p) ? <Button onClick={() => void api.simulate(p.id, "success").then(intents.reload, (e: unknown) => setMsg({ ok: false, text: problem(e) }))}>Completar pago simulado</Button> : null}
            </li>
          ))}
        </ul>
      </Status>
      <h2 className="text-base font-semibold">Recibos de caja</h2>
      <Status load={receipts} empty={(receipts.data?.items ?? []).length === 0}>
        <DocTable caption="Mis recibos de caja" rows={receipts.data?.items ?? []} />
      </Status>
    </Section>
  );
}

function today(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Bogota" });
}

export function StatementPage() {
  const api = useCustomerApi();
  const [range, setRange] = useState({ from: `${today().slice(0, 4)}-01-01`, to: today() });
  const load = useLoad((signal) => api.statement(range.from, range.to, signal), [range.from, range.to]);
  const st = load.data;
  return (
    <Section title="Estado de cuenta">
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">
          Desde
          <input className="nx-input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1">
          Hasta
          <input className="nx-input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} />
        </label>
        <a href={api.statementUrl(range.from, range.to, "pdf")} download>Descargar PDF</a>
        <a href={api.statementUrl(range.from, range.to, "csv")} download>Descargar CSV</a>
      </div>
      <Status load={load} empty={!st || st.rows.length === 0}>
        {st ? (
          <>
            <p className="text-sm">Saldo inicial: <span className="tabular-nums">{st.totals.opening_balance}</span> COP</p>
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Movimientos del estado de cuenta</caption>
              <thead>
                <tr>
                  <th scope="col">Fecha</th>
                  <th scope="col">Documento</th>
                  <th scope="col" className="text-right">Cargos</th>
                  <th scope="col" className="text-right">Abonos</th>
                  <th scope="col" className="text-right">Saldo</th>
                </tr>
              </thead>
              <tbody>
                {st.rows.map((r, i) => (
                  <tr key={`${r.number}-${i}`}>
                    <td>{r.date}</td>
                    <td>{r.number}</td>
                    <td className="text-right tabular-nums">{r.debit}</td>
                    <td className="text-right tabular-nums">{r.credit}</td>
                    <td className="text-right tabular-nums">{r.balance}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-sm font-semibold">Saldo final: <span className="tabular-nums">{st.totals.closing_balance}</span> COP</p>
          </>
        ) : null}
      </Status>
    </Section>
  );
}

export function RequestsPage() {
  const api = useCustomerApi();
  const load = useLoad((signal) => api.requests(signal));
  const [type, setType] = useState<RequestType>("QUOTE");
  const [message, setMessage] = useState("");
  const [item, setItem] = useState({ item_id: "", quantity: "" });
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<Msg>(null);
  async function send() {
    const { body, error } = requestBody(type, message, [item], changes);
    if (!body) {
      setMsg({ ok: false, text: error ?? "Revise los datos." });
      return;
    }
    try {
      const out = await api.createRequest(body);
      setMsg({ ok: true, text: `Solicitud ${out.number} enviada.` });
      setMessage("");
      setItem({ item_id: "", quantity: "" });
      setChanges({});
      load.reload();
    } catch (e) {
      setMsg({ ok: false, text: problem(e) });
    }
  }
  const rows = load.data?.items ?? [];
  return (
    <Section title="Solicitudes">
      <form aria-label="Nueva solicitud" className="nx-card flex flex-col gap-3 p-4" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <label className="flex max-w-xs flex-col gap-1 text-sm">
          Tipo de solicitud
          <select className="nx-input" value={type} onChange={(e) => setType(e.target.value as RequestType)}>
            {(Object.keys(REQUEST_TYPE_LABEL) as RequestType[]).map((k) => (
              <option key={k} value={k}>{REQUEST_TYPE_LABEL[k]}</option>
            ))}
          </select>
        </label>
        {type === "QUOTE" ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm">
              Código del producto
              <input className="nx-input" value={item.item_id} onChange={(e) => setItem({ ...item, item_id: e.target.value })} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              Cantidad
              <input className="nx-input" inputMode="decimal" value={item.quantity} onChange={(e) => setItem({ ...item, quantity: e.target.value })} />
            </label>
          </div>
        ) : null}
        {type === "PROFILE_CHANGE"
          ? Object.entries(PROFILE_FIELDS).map(([k, label]) => (
              <label key={k} className="flex max-w-xl flex-col gap-1 text-sm">
                {label}
                <input className="nx-input" value={changes[k] ?? ""} maxLength={254} onChange={(e) => setChanges({ ...changes, [k]: e.target.value })} />
              </label>
            ))
          : null}
        <label className="flex max-w-xl flex-col gap-1 text-sm">
          Mensaje
          <textarea className="nx-input" value={message} maxLength={10000} onChange={(e) => setMessage(e.target.value)} />
        </label>
        <div><Button variant="primary" type="submit">Enviar solicitud</Button></div>
        <Notice msg={msg} />
      </form>
      <Status load={load} empty={rows.length === 0}>
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Mis solicitudes</caption>
          <thead>
            <tr>
              <th scope="col">Número</th>
              <th scope="col">Tipo</th>
              <th scope="col">Estado</th>
              <th scope="col">Respuesta</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.number}</td>
                <td>{REQUEST_TYPE_LABEL[r.request_type]}</td>
                <td><Badge>{REQUEST_STATUS_LABEL[r.request_status] ?? r.request_status}</Badge></td>
                <td>{r.resolution || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Status>
    </Section>
  );
}

export function ProfilePage() {
  const api = useCustomerApi();
  const load = useLoad((signal) => api.profile(signal));
  const p = load.data;
  return (
    <Section title="Perfil">
      <Status load={load} empty={!p}>
        {p ? (
          <dl className="grid max-w-xl grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="font-medium">Razón social</dt>
            <dd>{p.legal_name}</dd>
            <dt className="font-medium">NIT</dt>
            <dd>{p.nit}</dd>
            <dt className="font-medium">Correo</dt>
            <dd>{p.email ?? "—"}</dd>
          </dl>
        ) : null}
      </Status>
      <p className="text-sm">Para actualizar sus datos, envíe una solicitud de «Cambio de datos»; un analista la revisará y aprobará.</p>
    </Section>
  );
}
