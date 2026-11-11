// Tesorería › Recibos de caja / Egresos (F-TRE-RC / F-TRE-CE): list with status chips and totals, entry form (party,
// fund, applications to open items, advances, concept lines, withholding lines), journal preview (:simulate), approval
// and posting actions, reversal with a reason. Every control is a native, labelled element (keyboard + screen reader).
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { Field, Input, Select } from "@ds/components/Field";
import { formatCOP, sumDecimals } from "@lib/money";
import type { FundDto, SimulationDto, VoucherDto, VoucherKind, VoucherLine, PaymentMethod } from "../api";
import { ACTION_LABEL, KIND_LABEL, METHOD_LABEL, NEEDS_REASON, STATUS_LABEL, STATUS_TONE, allowedActions, lineKind, type ActionId } from "../model";
import { useTreasuryApi } from "../useTreasuryApi";

interface Props {
  kind: VoucherKind;
  title: string;
  perm: "treasury.receipt" | "treasury.disbursement";
}

interface DraftLine {
  type: "APLICACION" | "CONCEPTO" | "RETENCION" | "ANTICIPO";
  ref: string;
  amount: string;
}

const today = () => new Date().toISOString().slice(0, 10);

function toLine(n: number, l: DraftLine): VoucherLine {
  const money = { amount: l.amount, currency: "COP" };
  if (l.type === "APLICACION") return { line_no: n, open_item_id: l.ref, applied_amount: money };
  if (l.type === "CONCEPTO") return { line_no: n, concept_id: l.ref, applied_amount: money };
  if (l.type === "RETENCION") return { line_no: n, tax_code: l.ref, unit_price: money };
  return { line_no: n, applied_amount: money, description: "Anticipo" };
}

export default function VouchersPage({ kind, title, perm }: Props) {
  const { api, companyId, can } = useTreasuryApi();
  const [rows, setRows] = useState<VoucherDto[] | null>(null);
  const [funds, setFunds] = useState<FundDto[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<SimulationDto | null>(null);
  const [reason, setReason] = useState("");
  const [party, setParty] = useState("");
  const [fund, setFund] = useState("");
  const [method, setMethod] = useState<PaymentMethod>("TRANSFER");
  const [lines, setLines] = useState<DraftLine[]>([{ type: "APLICACION", ref: "", amount: "" }]);

  const reload = useCallback(() => {
    let live = true;
    setRows(null);
    api.vouchers(kind).then(
      (p) => live && setRows(p.items),
      () => live && setRows([]),
    );
    api.funds().then(
      (p) => live && setFunds(p.items),
      () => live && setFunds([]),
    );
    return () => {
      live = false;
    };
  }, [api, kind]);
  useEffect(() => reload(), [reload, companyId]);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    }
  };

  const create = () =>
    run(() =>
      api.createVoucher(kind, {
        document_date: today(),
        fund_id: fund,
        payment_method: method,
        currency: "COP",
        ...(party ? { party: { id: party, kind: kind === "receipts" ? "customer" : "vendor" } } : {}),
        lines: lines.filter((l) => l.amount).map((l, i) => toLine(i + 1, l)),
      }),
    );

  const act = (v: VoucherDto, action: ActionId) => {
    if (NEEDS_REASON.has(action) && !reason.trim()) {
      setError("Escriba el motivo antes de rechazar o reversar.");
      return;
    }
    void run(() => api.act(kind, v.id, action, NEEDS_REASON.has(action) ? reason : ""));
  };

  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando" />;
  const posted = rows.filter((r) => r.status === "POSTED").map((r) => r.total.amount);
  return (
    <section aria-label={title} className="space-y-4">
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
      {can(`${perm}.create`) && (
        <form
          className="nx-card space-y-2"
          aria-label={`Nuevo ${title.toLowerCase()}`}
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <Field label="Tercero (id)">{(p) => <Input id={p.id} value={party} onChange={(e) => setParty(e.target.value)} />}</Field>
          <Field label="Fondo (banco o caja)" required>
            {(p) => (
              <Select id={p.id} value={fund} onChange={(e) => setFund(e.target.value)} required>
                <option value="">Seleccione…</option>
                {funds.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.code} · {f.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Medio de pago">
            {(p) => (
              <Select id={p.id} value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod)}>
                {Object.entries(METHOD_LABEL).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <fieldset>
            <legend>Líneas</legend>
            {lines.map((l, i) => (
              <div key={i} className="flex gap-2">
                <Field label={`Tipo línea ${i + 1}`}>
                  {(p) => (
                    <Select id={p.id} value={l.type} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, type: e.target.value as DraftLine["type"] } : x)))}>
                      {Object.entries(KIND_LABEL).map(([k, label]) => (
                        <option key={k} value={k}>
                          {label}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Field label={l.type === "RETENCION" ? "Código de retención" : "Partida / concepto (id)"}>
                  {(p) => <Input id={p.id} value={l.ref} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, ref: e.target.value } : x)))} />}
                </Field>
                <Field label={l.type === "RETENCION" ? "Base" : "Valor"}>
                  {(p) => <Input id={p.id} inputMode="decimal" value={l.amount} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} />}
                </Field>
              </div>
            ))}
            <Button type="button" variant="ghost" onClick={() => setLines([...lines, { type: "ANTICIPO", ref: "", amount: "" }])}>
              Agregar línea
            </Button>
          </fieldset>
          <Button type="submit">Guardar borrador</Button>
        </form>
      )}
      {!rows.length ? (
        <EmptyState title={`Aún no hay ${title.toLowerCase()}`} body="Cree un borrador; el número se asigna al contabilizar." />
      ) : (
        <>
          <Field label="Motivo (rechazo / reversión)">{(p) => <Input id={p.id} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
          <table className="nx-card w-full text-sm" aria-label={title}>
            <caption className="text-left text-xs text-muted">Total contabilizado: {formatCOP(sumDecimals(posted))}</caption>
            <thead>
              <tr className="text-left text-xs text-muted">
                <th scope="col">Número</th>
                <th scope="col">Fecha</th>
                <th scope="col">Estado</th>
                <th scope="col">Líneas</th>
                <th scope="col">Total</th>
                <th scope="col">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((v) => (
                <tr key={v.id}>
                  <td>{v.number || "—"}</td>
                  <td>{v.document_date}</td>
                  <td>
                    <Badge tone={STATUS_TONE[v.status]}>{STATUS_LABEL[v.status]}</Badge>
                  </td>
                  <td>{v.lines.map((l) => KIND_LABEL[lineKind(l)]).join(", ")}</td>
                  <td className="text-right tabular-nums">{formatCOP(v.total.amount)}</td>
                  <td className="flex gap-1">
                    <Button type="button" variant="ghost" onClick={() => void api.simulate(kind, v.id).then(setPreview, (e: Error) => setError(e.message))}>
                      Vista previa
                    </Button>
                    {allowedActions(v, can, perm).map((a) => (
                      <Button key={a} type="button" variant={a === "reverse" || a === "reject" ? "danger" : "secondary"} onClick={() => act(v, a)}>
                        {ACTION_LABEL[a]}
                      </Button>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {preview && (
        <table className="nx-card w-full text-sm" aria-label="Vista previa del asiento">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th scope="col">Cuenta</th>
              <th scope="col">Débito</th>
              <th scope="col">Crédito</th>
            </tr>
          </thead>
          <tbody>
            {preview.journal_entries.flatMap((e) =>
              e.lines.map((l, i) => (
                <tr key={`${e.journal}-${i}`}>
                  <td>{l.account}</td>
                  <td className="text-right tabular-nums">{formatCOP(l.debit.amount)}</td>
                  <td className="text-right tabular-nums">{formatCOP(l.credit.amount)}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      )}
    </section>
  );
}
