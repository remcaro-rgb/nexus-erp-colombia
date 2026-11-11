// POS view model (owner P12) — pure, decimal-safe (strings + Dec), no I/O.
// ADR-015 online finalization: the client NEVER finalizes, numbers or signs a sale. A cart is a local, unposted
// DRAFT ("BORRADOR – sin valor fiscal") until POST /api/v1/pos/tickets:checkout commits on the server; the receipt is
// printable only from the committed server response. While the server is unreachable every finalizing action is
// disabled (checkout, tenders, returns, cash movements, session close, receipt print) and nothing is queued.
import { Dec } from "@lib/money";

export const DRAFT_WATERMARK = "BORRADOR – sin valor fiscal";
export const OFFLINE_BANNER = "Sin conexión: no se pueden finalizar ventas";

export type Connectivity = "online" | "offline";

export interface CatalogPresentation {
  id: string;
  code: string;
  name: string;
  factor: string;
  barcode: string | null;
  precision: number;
}

export interface CatalogItem {
  id: string;
  code: string;
  name: string;
  category?: string;
  uom: string;
  gtin: string | null;
  tax_code: string | null;
  price: string | null;
  presentations: CatalogPresentation[];
}

export interface CartLine {
  key: string;
  item_id: string;
  presentation_id: string | null;
  barcode: string | null;
  description: string;
  quantity: string; // sold units (packages for a presentation)
  factor: string;
  unit_price: string | null; // display price from the read-only catalog (the server recomputes)
}

export interface DraftCart {
  client_uuid: string; // also the checkout Idempotency-Key
  session_id: string | null;
  lines: CartLine[];
  status: "draft";
  updated_at: string;
}

export interface Tender {
  type: "CASH" | "CARD" | "TRANSFER" | "QR" | "VOUCHER";
  amount: string;
  reference?: string;
}

export interface PosActions {
  checkout: boolean;
  tender: boolean;
  printReceipt: boolean;
  returns: boolean;
  cashMovements: boolean;
  closeSession: boolean;
  /** Always allowed: browsing the cached catalog and building / parking local drafts. */
  buildCart: boolean;
  parkDraft: boolean;
}

/** What the counter may do in the current connectivity state (T-P12-04: disabled while disconnected). */
export function posActions(connectivity: Connectivity, opts: { sessionOpen: boolean; committedReceipt: boolean }): PosActions {
  const online = connectivity === "online";
  return {
    checkout: online && opts.sessionOpen,
    tender: online && opts.sessionOpen,
    printReceipt: online && opts.committedReceipt,
    returns: online && opts.sessionOpen,
    cashMovements: online && opts.sessionOpen,
    closeSession: online && opts.sessionOpen,
    buildCart: true,
    parkDraft: true,
  };
}

function normalizeScan(raw: string): string {
  return raw.replace(/[\r\n\t]/g, "").trim();
}

function gtinVariants(value: string): string[] {
  if (!/^\d+$/.test(value)) return [value];
  const g14 = value.padStart(14, "0");
  return Array.from(new Set([value, g14, g14.slice(1), g14.slice(2), g14.replace(/^0+/, "")]));
}

export interface ScanMatch {
  item: CatalogItem;
  presentation: CatalogPresentation | null;
  value: string;
}

/** Keyboard-wedge scan → catalog match; a presentation barcode wins over the item GTIN (T-INV-BARCODE-05). */
export function resolveScan(catalog: readonly CatalogItem[], raw: string): ScanMatch | null {
  const value = normalizeScan(raw);
  if (!value) return null;
  const variants = gtinVariants(value);
  for (const item of catalog) {
    const pres = item.presentations.find((p) => p.barcode && variants.includes(p.barcode));
    if (pres) return { item, presentation: pres, value };
  }
  const item = catalog.find((i) => i.gtin && variants.includes(i.gtin));
  return item ? { item, presentation: null, value } : null;
}

function displayPrice(item: CatalogItem, presentation: CatalogPresentation | null): string | null {
  if (item.price === null) return null;
  const factor = presentation ? presentation.factor : "1";
  return new Dec(item.price).mul(factor).toDecimalPlaces(2).toFixed(2);
}

/** Adds the scanned product to the cart: the same item + presentation increments, otherwise a new line. */
export function addScan(cart: DraftCart, match: ScanMatch, now = new Date().toISOString()): DraftCart {
  const presId = match.presentation?.id ?? null;
  const existing = cart.lines.find((l) => l.item_id === match.item.id && l.presentation_id === presId);
  let lines = [...cart.lines];
  if (existing) {
    lines = lines.map((l) => (l === existing ? { ...l, quantity: new Dec(l.quantity).add(1).toFixed() } : l));
  } else {
    lines.push({
      key: `${match.item.id}|${presId ?? "base"}`,
      item_id: match.item.id,
      presentation_id: presId,
      barcode: match.value,
      description: match.presentation ? `${match.item.name} — ${match.presentation.name}` : match.item.name,
      quantity: "1",
      factor: match.presentation ? new Dec(match.presentation.factor).toFixed() : "1",
      unit_price: displayPrice(match.item, match.presentation),
    });
  }
  return { ...cart, lines, updated_at: now };
}

export function newDraft(clientUuid: string, sessionId: string | null, now = new Date().toISOString()): DraftCart {
  return { client_uuid: clientUuid, session_id: sessionId, lines: [], status: "draft", updated_at: now };
}

export function setQuantity(cart: DraftCart, key: string, quantity: string): DraftCart {
  const q = new Dec(quantity || "0");
  const lines = q.lte(0) ? cart.lines.filter((l) => l.key !== key) : cart.lines.map((l) => (l.key === key ? { ...l, quantity: q.toFixed() } : l));
  return { ...cart, lines };
}

/** Display subtotal of the draft (before tax) — informative only; the server total is authoritative. */
export function draftSubtotal(cart: DraftCart): string {
  return cart.lines
    .reduce((acc, l) => (l.unit_price === null ? acc : acc.add(new Dec(l.unit_price).mul(l.quantity))), new Dec(0))
    .toFixed(2);
}

/** Body of POST /tickets:checkout (ActionRequest.parameters); the server recomputes everything. */
export function checkoutParameters(cart: DraftCart, tenders: Tender[], expectedTotal?: string) {
  return {
    client_uuid: cart.client_uuid,
    session_id: cart.session_id,
    lines: cart.lines.map((l) =>
      l.presentation_id && l.barcode
        ? { barcode: l.barcode, quantity: l.quantity, ...(l.unit_price ? { unit_price: l.unit_price } : {}) }
        : { item_id: l.item_id, quantity: l.quantity, ...(l.presentation_id ? { presentation_id: l.presentation_id } : {}), ...(l.unit_price ? { unit_price: l.unit_price } : {}) },
    ),
    payments: tenders.map((t) => ({ type: t.type, amount: t.amount, ...(t.reference ? { reference: t.reference } : {}) })),
    ...(expectedTotal ? { expected_total: expectedTotal } : {}),
  };
}

export interface ServerDiffLine {
  line_no: number;
  item_id: string;
  draft_unit_price: string;
  server_unit_price: string;
}

export interface PriceChange {
  total: string;
  diff: ServerDiffLine[];
}

/** 409 pos_price_changed → what the cashier must confirm before the commit (ADR-015 reconnect rule). */
export function priceChangeFromProblem(problem: { code?: string; errors?: unknown } | null | undefined): PriceChange | null {
  if (!problem || problem.code !== "pos_price_changed") return null;
  const server = (problem.errors as { server?: { total?: string; diff?: ServerDiffLine[] } } | undefined)?.server;
  if (!server?.total) return null;
  return { total: server.total, diff: server.diff ?? [] };
}

export interface CommittedTicket {
  id: string;
  number: string;
  status: string;
  total: { amount: string; currency: string } | null;
  change_due?: { amount: string; currency: string } | null;
  cude?: string | null;
  fiscal_status?: string;
  lines: Array<{ line_no: number; description: string; quantity: { value: string }; amount: { amount: string } | null }>;
}

export interface ReceiptLine {
  text: string;
  amount: string;
}

export interface Receipt {
  title: string;
  number: string;
  copy: boolean;
  lines: ReceiptLine[];
  total: string;
  change: string | null;
  qr: string | null;
  note: string;
}

/** Receipt from the COMMITTED server response only (T-P12-11): drafts have no receipt. */
export function receiptFor(ticket: CommittedTicket | null, opts: { copy?: boolean } = {}): Receipt | null {
  if (!ticket || ticket.status !== "POSTED" || !ticket.number) return null;
  return {
    title: "Documento equivalente electrónico POS",
    number: ticket.number,
    copy: Boolean(opts.copy),
    lines: ticket.lines.map((l) => ({ text: `${l.quantity.value} × ${l.description}`, amount: l.amount?.amount ?? "0" })),
    total: ticket.total?.amount ?? "0",
    change: ticket.change_due?.amount ?? null,
    qr: ticket.cude ?? null,
    note: ticket.cude ? "" : "CUDE pendiente de validación (simulador DEV)",
  };
}

export interface KeyBinding {
  key: string;
  action: "search" | "checkout" | "park" | "cash" | "card" | "clear";
  label: string;
}

/** F-key shortcuts of the counter (UX); actions that finalize are still gated by posActions(). */
export const SHORTCUTS: readonly KeyBinding[] = [
  { key: "F2", action: "search", label: "Buscar producto" },
  { key: "F4", action: "park", label: "Aparcar borrador" },
  { key: "F8", action: "cash", label: "Pago en efectivo" },
  { key: "F9", action: "card", label: "Pago con tarjeta" },
  { key: "F12", action: "checkout", label: "Finalizar venta" },
  { key: "Escape", action: "clear", label: "Vaciar carrito" },
];
