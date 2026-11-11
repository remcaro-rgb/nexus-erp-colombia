/** Pages of Punto de venta (owner P12): Vender (focused counter), Turnos y arqueos, Tiquetes.
 * Data only through the public API, company-scoped; stale responses are dropped on a company switch.
 * ADR-015: a sale is final only when POST /tickets:checkout commits; while the server is unreachable the counter shows
 * the blocking banner, keeps building/parking LOCAL drafts and disables every finalizing action (nothing is queued). */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatCOP } from "@lib/money";
import { newIdempotencyKey } from "@lib/ids";
import { can } from "@lib/permissions";
import { useConnectivity } from "@lib/offline/connectivity";
import { NexusOfflineDb, OfflineStore } from "@lib/offline/db";
import { useShell } from "@shell/ShellContext";
import type { PageComponent } from "@shell/registry";
import { posApi, type PosSession, type PosTerminal } from "./api";
import {
  DRAFT_WATERMARK,
  OFFLINE_BANNER,
  SHORTCUTS,
  addScan,
  checkoutParameters,
  draftSubtotal,
  newDraft,
  posActions,
  priceChangeFromProblem,
  receiptFor,
  resolveScan,
  setQuantity,
  type CatalogItem,
  type CommittedTicket,
  type DraftCart,
  type PriceChange,
  type Tender,
} from "./model";

function usePos() {
  const shell = useShell();
  const companyId = shell.companyId ?? "";
  const api = useMemo(() => posApi(shell.api, companyId), [shell.api, companyId]);
  return { shell, companyId, api };
}

function errorText(e: unknown): string {
  const err = e as { code?: string; message?: string };
  return err?.code ? `${err.code}: ${err.message ?? ""}` : String(e);
}

function isNetworkError(e: unknown): boolean {
  return e instanceof TypeError || (e as { name?: string })?.name === "TypeError";
}

function offlineStore(tenant: string, company: string): OfflineStore | null {
  try {
    return typeof indexedDB === "undefined" ? null : new OfflineStore(new NexusOfflineDb(), tenant, company);
  } catch {
    return null;
  }
}

const btn = "min-h-11 min-w-11 rounded-md px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50"; // WCAG 2.5.8

export function SellPage() {
  const { shell, companyId, api } = usePos();
  const browser = useConnectivity();
  const [serverDown, setServerDown] = useState(false);
  const connectivity = browser === "offline" || serverDown ? "offline" : "online";
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [session, setSession] = useState<PosSession | null>(null);
  const [cart, setCart] = useState<DraftCart>(() => newDraft(newIdempotencyKey(), null));
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [committed, setCommitted] = useState<CommittedTicket | null>(null);
  const [change, setChange] = useState<PriceChange | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [parked, setParked] = useState<DraftCart[]>([]);
  const scanRef = useRef<HTMLInputElement>(null);
  const store = useMemo(() => offlineStore(shell.context?.tenant_id ?? "tenant", companyId), [shell.context, companyId]);
  const actions = posActions(connectivity, { sessionOpen: session?.status === "OPEN", committedReceipt: committed !== null });

  useEffect(() => {
    if (!companyId || shell.switching) return;
    const controller = new AbortController();
    setSession(null);
    api
      .sessions({ status: "OPEN", cashier_id: shell.userId }, controller.signal)
      .then((p) => !controller.signal.aborted && setSession(p.items[0] ?? null))
      .catch((e: unknown) => !controller.signal.aborted && (isNetworkError(e) ? setServerDown(true) : setMessage(errorText(e))));
    api
      .catalog(undefined, controller.signal)
      .then((r) => {
        if (controller.signal.aborted) return;
        setCatalog(r.rows);
        void store?.putCatalog("pos-items", r.rows as unknown as Array<{ id: string } & Record<string, unknown>>, new Date().toISOString());
      })
      .catch(() => store?.listCatalog<CatalogItem>("pos-items").then((rows) => !controller.signal.aborted && setCatalog(rows)));
    store?.listDrafts("pos-cart").then((rows) => !controller.signal.aborted && setParked(rows.map((r) => r.payload as DraftCart))).catch(() => undefined);
    return () => controller.abort();
  }, [api, companyId, shell.switching, shell.userId, store]);

  useEffect(() => {
    setCart((c) => ({ ...c, session_id: session?.id ?? null }));
  }, [session]);

  const onScan = useCallback(
    (raw: string) => {
      const match = resolveScan(catalog, raw);
      if (!match) {
        setMessage(`Código no reconocido: ${raw.trim()}`);
        return;
      }
      setCommitted(null);
      setCart((c) => addScan(c, match));
    },
    [catalog],
  );

  const park = () => {
    if (cart.lines.length === 0) return;
    void store?.saveDraft("pos-cart", cart.client_uuid, cart).catch(() => undefined);
    setParked((p) => [...p.filter((d) => d.client_uuid !== cart.client_uuid), cart]);
    setCart(newDraft(newIdempotencyKey(), session?.id ?? null));
    setTenders([]);
  };

  const resume = (draft: DraftCart) => {
    setCart({ ...draft, session_id: session?.id ?? null });
    setParked((p) => p.filter((d) => d.client_uuid !== draft.client_uuid));
  };

  const checkout = (expectedTotal?: string) => {
    if (!actions.checkout || cart.lines.length === 0) return;
    setMessage(null);
    api
      .checkout(cart.client_uuid, checkoutParameters(cart, tenders, expectedTotal))
      .then((res) => {
        setServerDown(false);
        setCommitted(res.data); // the receipt exists only from this committed response
        setChange(null);
        void store?.deleteDraft("pos-cart", cart.client_uuid).catch(() => undefined);
        setCart(newDraft(newIdempotencyKey(), session?.id ?? null));
        setTenders([]);
      })
      .catch((e: unknown) => {
        if (isNetworkError(e)) {
          setServerDown(true); // nothing was finalized: the cart stays a local draft
          return;
        }
        const pc = priceChangeFromProblem((e as { problem?: { code?: string; errors?: unknown } }).problem);
        if (pc) setChange(pc);
        else setMessage(errorText(e));
      });
  };

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const binding = SHORTCUTS.find((s) => s.key === ev.key);
      if (!binding) return;
      ev.preventDefault();
      if (binding.action === "search") scanRef.current?.focus();
      if (binding.action === "park") park();
      if (binding.action === "checkout") checkout();
      if (binding.action === "clear") setCart(newDraft(newIdempotencyKey(), session?.id ?? null));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const receipt = receiptFor(committed);
  const subtotal = draftSubtotal(cart);
  return (
    <section className="flex h-full flex-col gap-3" aria-labelledby="pos-sell-title">
      <h1 id="pos-sell-title" className="text-lg font-semibold">Vender</h1>
      {connectivity === "offline" && (
        <div role="alert" data-testid="pos-offline-banner" className="rounded-md bg-red-700 px-4 py-3 text-sm font-semibold text-white">
          {OFFLINE_BANNER}
        </div>
      )}
      {!session && <p className="text-sm">No hay un turno abierto para su usuario. Abra la caja en «Turnos y arqueos».</p>}
      {message && <p role="alert" className="text-sm text-red-700">{message}</p>}
      <form
        onSubmit={(ev) => {
          ev.preventDefault();
          const input = scanRef.current;
          if (input?.value) onScan(input.value);
          if (input) input.value = "";
        }}
      >
        <label className="flex flex-col gap-1 text-sm" htmlFor="pos-scan">
          Escanear o digitar código (F2)
          <input id="pos-scan" ref={scanRef} autoComplete="off" className="min-h-11 rounded-md border px-3" data-testid="pos-scan" />
        </label>
      </form>
      <div className="relative rounded-md border p-3" data-testid="pos-cart">
        {cart.lines.length > 0 && (
          <span aria-hidden="true" className="pointer-events-none absolute right-3 top-2 text-xs font-bold uppercase text-amber-700">
            {DRAFT_WATERMARK}
          </span>
        )}
        <table className="w-full text-sm">
          <caption className="sr-only">Carrito (borrador sin valor fiscal)</caption>
          <thead>
            <tr>
              <th scope="col" className="text-left">Producto</th>
              <th scope="col">Cantidad</th>
              <th scope="col" className="text-right">Precio ref.</th>
            </tr>
          </thead>
          <tbody>
            {cart.lines.map((l) => (
              <tr key={l.key}>
                <td>{l.description}</td>
                <td className="text-center">
                  <input
                    aria-label={`Cantidad de ${l.description}`}
                    className="min-h-11 w-20 rounded border text-center"
                    inputMode="numeric"
                    value={l.quantity}
                    onChange={(e) => setCart((c) => setQuantity(c, l.key, e.target.value))}
                  />
                </td>
                <td className="text-right">{l.unit_price ? formatCOP(l.unit_price) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-right text-sm">Subtotal de referencia: {formatCOP(subtotal)} (el servidor recalcula precios e impuestos)</p>
      </div>
      {change && (
        <div role="alertdialog" aria-labelledby="pos-diff-title" className="rounded-md border border-amber-600 p-3 text-sm">
          <h2 id="pos-diff-title" className="font-semibold">El servidor recalculó la venta</h2>
          <ul>
            {change.diff.map((d) => (
              <li key={d.line_no}>Línea {d.line_no}: {formatCOP(d.draft_unit_price)} → {formatCOP(d.server_unit_price)}</li>
            ))}
          </ul>
          <p>Nuevo total: {formatCOP(change.total)}</p>
          <button type="button" className={`${btn} bg-amber-600 text-white`} disabled={!actions.checkout} onClick={() => checkout(change.total)}>
            Confirmar nuevo total
          </button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={`${btn} border`} disabled={!actions.tender} onClick={() => setTenders([{ type: "CASH", amount: subtotal }])}>
          Efectivo (F8)
        </button>
        <button type="button" className={`${btn} border`} disabled={!actions.tender} onClick={() => setTenders([{ type: "CARD", amount: subtotal, reference: "" }])}>
          Tarjeta (F9)
        </button>
        <button type="button" className={`${btn} border`} disabled={!actions.parkDraft || cart.lines.length === 0} onClick={park}>
          Aparcar (F4)
        </button>
        <button
          type="button"
          data-testid="pos-checkout"
          className={`${btn} bg-emerald-700 text-white`}
          disabled={!actions.checkout || cart.lines.length === 0 || tenders.length === 0}
          onClick={() => checkout()}
        >
          Finalizar venta (F12)
        </button>
      </div>
      {parked.length > 0 && (
        <section aria-label="Borradores aparcados" className="text-sm">
          <h2 className="font-semibold">Borradores aparcados ({DRAFT_WATERMARK})</h2>
          <ul>
            {parked.map((d) => (
              <li key={d.client_uuid}>
                <button type="button" className={`${btn} border`} onClick={() => resume(d)}>
                  Retomar borrador de {d.lines.length} línea(s)
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {receipt && (
        <section aria-label="Recibo" className="rounded-md border p-3 text-sm" data-testid="pos-receipt">
          <h2 className="font-semibold">
            {receipt.title} {receipt.number}
          </h2>
          <ul>
            {receipt.lines.map((l, i) => (
              <li key={i}>
                {l.text} — {formatCOP(l.amount)}
              </li>
            ))}
          </ul>
          <p>Total: {formatCOP(receipt.total)}</p>
          {receipt.change && <p>Cambio: {formatCOP(receipt.change)}</p>}
          {receipt.note && <p className="text-xs">{receipt.note}</p>}
          <button type="button" className={`${btn} border`} disabled={!actions.printReceipt} onClick={() => window.print()}>
            Imprimir recibo
          </button>
        </section>
      )}
    </section>
  );
}

export function SessionsPage() {
  const { shell, companyId, api } = usePos();
  const connectivity = useConnectivity();
  const [sessions, setSessions] = useState<PosSession[]>([]);
  const [terminals, setTerminals] = useState<PosTerminal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [opening, setOpening] = useState("0");
  const [counted, setCounted] = useState("");

  useEffect(() => {
    if (!companyId || shell.switching) return;
    const controller = new AbortController();
    setSessions([]);
    api.sessions({}, controller.signal).then((p) => !controller.signal.aborted && setSessions(p.items)).catch((e: unknown) => !controller.signal.aborted && setError(errorText(e)));
    if (can(shell.perms, "pos.terminal.read")) {
      api.terminals(controller.signal).then((p) => !controller.signal.aborted && setTerminals(p.items)).catch(() => undefined);
    }
    return () => controller.abort();
  }, [api, companyId, nonce, shell.switching, shell.perms]);

  const online = connectivity === "online";
  const act = (p: Promise<unknown>) => p.then(() => setNonce((n) => n + 1)).catch((e: unknown) => setError(errorText(e)));
  const open = sessions.find((s) => s.status === "OPEN" && s.cashier_id === shell.userId);
  return (
    <section className="flex flex-col gap-3" aria-labelledby="pos-sessions-title">
      <h1 id="pos-sessions-title" className="text-lg font-semibold">Turnos y arqueos</h1>
      {!online && <div role="alert" className="rounded-md bg-red-700 px-4 py-2 text-sm text-white">{OFFLINE_BANNER}</div>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {!open && terminals[0] && (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (terminals[0]) act(api.openSession(terminals[0].id, opening)); }}>
          <label className="flex flex-col text-sm">
            Base de caja (COP)
            <input className="min-h-11 rounded border px-2" inputMode="decimal" value={opening} onChange={(e) => setOpening(e.target.value)} />
          </label>
          <button type="submit" className={`${btn} bg-emerald-700 text-white`} disabled={!online}>Abrir caja</button>
        </form>
      )}
      {open && (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); act(api.closeSession(open.id, { CASH: counted })); }}>
          <label className="flex flex-col text-sm">
            Efectivo contado (arqueo ciego)
            <input className="min-h-11 rounded border px-2" inputMode="decimal" value={counted} onChange={(e) => setCounted(e.target.value)} />
          </label>
          <button type="submit" className={`${btn} border`} disabled={!online || counted === ""}>Cerrar turno</button>
        </form>
      )}
      <table className="w-full text-sm">
        <caption className="sr-only">Turnos de caja</caption>
        <thead>
          <tr><th scope="col" className="text-left">Turno</th><th scope="col">Estado</th><th scope="col">Apertura</th><th scope="col" className="text-right">Diferencia</th></tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.id}>
              <td>{s.number}</td>
              <td>{s.status === "OPEN" ? "Abierto" : s.status === "CLOSED" ? "Cerrado" : s.status}</td>
              <td>{new Date(s.opened_at).toLocaleString("es-CO", { timeZone: "America/Bogota" })}</td>
              <td className="text-right">{s.difference ? formatCOP(s.difference.amount) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {sessions.length === 0 && <p className="text-sm">Sin turnos registrados.</p>}
    </section>
  );
}

export function TicketsPage() {
  const { shell, companyId, api } = usePos();
  const [tickets, setTickets] = useState<CommittedTicket[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!companyId || shell.switching) return;
    const controller = new AbortController();
    setTickets([]);
    api.tickets({ status: "POSTED" }, controller.signal).then((p) => !controller.signal.aborted && setTickets(p.items)).catch((e: unknown) => !controller.signal.aborted && setError(errorText(e)));
    return () => controller.abort();
  }, [api, companyId, shell.switching]);
  return (
    <section className="flex flex-col gap-3" aria-labelledby="pos-tickets-title">
      <h1 id="pos-tickets-title" className="text-lg font-semibold">Tiquetes</h1>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <table className="w-full text-sm">
        <caption className="sr-only">Tiquetes finalizados</caption>
        <thead>
          <tr><th scope="col" className="text-left">Número</th><th scope="col">Estado fiscal</th><th scope="col" className="text-right">Total</th></tr>
        </thead>
        <tbody>
          {tickets.map((t) => (
            <tr key={t.id}>
              <td>{t.number}</td>
              <td>{t.fiscal_status ?? "—"}</td>
              <td className="text-right">{t.total ? formatCOP(t.total.amount) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {tickets.length === 0 && !error && <p className="text-sm">Sin tiquetes finalizados en este periodo.</p>}
    </section>
  );
}

export const pages: Record<string, PageComponent> = { vender: SellPage, turnos: SessionsPage, tiquetes: TicketsPage };
