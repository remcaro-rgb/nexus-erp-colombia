// "Registro de activos" (/m/activos/registro) and "Nuevo activo" (/m/activos/registro/nuevo), owner P14.
// Rows come from GET /api/v1/assets/reports/register (one per asset and book); QR labels are rendered as a PDF
// from the same rows. Success messages only after the API confirms.
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { ApiError } from "@lib/api/client";
import { DataGrid, type GridColumn } from "@ds/data-grid";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { Field, Input, Select } from "@ds/components/Field";
import { useToast } from "@ds/components/Toast";
import { useResource, useShell } from "@shell/ShellContext";
import type { PageComponent } from "@shell/registry";
import { assetsApi, labelItems, problemText, registerRows, type AssetCategory, type Page, type RegisterRow, type ReportResult } from "./api";
import { labelsBlob } from "./labels";
import { msg } from "./messages";

function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function NewAssetForm({ onDone }: { onDone: () => void }) {
  const shell = useShell();
  const toast = useToast();
  const cats = useResource<Page<AssetCategory>>("/api/v1/assets/categories", { company_id: shell.companyId, limit: 500 });
  const [form, setForm] = useState({ code: "", name: "", category_id: "", cost: "", acquisition_date: "", in_service_date: "", counterpart_role: "AP" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  async function save() {
    const errs: Record<string, string> = {};
    if (!form.code.trim()) errs.code = msg.required;
    if (!form.name.trim()) errs.name = msg.required;
    if (!form.category_id) errs.category_id = msg.required;
    if (!/^\d{1,16}(\.\d{1,4})?$/.test(form.cost)) errs.cost = msg.amountInvalid;
    if (!form.acquisition_date) errs.acquisition_date = msg.required;
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    const client = assetsApi(shell.api, shell.companyId);
    try {
      const created = await client.createAsset({
        code: form.code.trim(),
        name: form.name.trim(),
        category_id: form.category_id,
        acquisition_cost: { amount: form.cost, currency: "COP" },
        acquisition_date: form.acquisition_date,
      });
      if (form.in_service_date) await client.activate(created.id, { in_service_date: form.in_service_date, counterpart_role: form.counterpart_role });
      toast.push({ tone: "success", title: msg.created(created.code) });
      onDone();
    } catch (e) {
      if (e instanceof ApiError) {
        setErrors(e.fieldErrors());
        toast.push({ tone: "error", title: problemText(e.code, e.message), traceId: e.traceId });
      } else throw e;
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label={msg.newAsset} className="flex max-w-2xl flex-col gap-4">
      <h2 className="text-lg font-semibold">{msg.newAsset}</h2>
      <p className="text-sm text-[var(--nx-color-text-muted)]">{msg.newAssetHint}</p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label={msg.code} error={errors.code} required>{(p) => <Input id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid || undefined} value={form.code} onChange={set("code")} maxLength={40} />}</Field>
        <Field label={msg.name} error={errors.name} required>{(p) => <Input id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid || undefined} value={form.name} onChange={set("name")} maxLength={200} />}</Field>
        <Field label={msg.category} error={errors.category_id} required>
          {(p) => (
            <Select id={p.id} aria-describedby={p.describedBy} value={form.category_id} onChange={set("category_id")}>
              <option value="">{msg.choose}</option>
              {(cats.data?.items ?? []).map((c) => (
                <option key={c.id} value={c.id}>{`${c.code} — ${c.name}`}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label={msg.cost} error={errors.cost} required hint="COP">{(p) => <Input id={p.id} inputMode="decimal" aria-describedby={p.describedBy} aria-invalid={p.invalid || undefined} value={form.cost} onChange={set("cost")} />}</Field>
        <Field label={msg.acquisitionDate} error={errors.acquisition_date} required>{(p) => <Input id={p.id} type="date" value={form.acquisition_date} onChange={set("acquisition_date")} />}</Field>
        <Field label={msg.inServiceDate} hint={msg.inServiceHint}>{(p) => <Input id={p.id} type="date" aria-describedby={p.describedBy} value={form.in_service_date} onChange={set("in_service_date")} />}</Field>
        <Field label={msg.counterpart} hint={msg.counterpartHint}>
          {(p) => (
            <Select id={p.id} aria-describedby={p.describedBy} value={form.counterpart_role} onChange={set("counterpart_role")}>
              {["AP", "BANK", "CIP", "OPENING_BALANCE_EQUITY"].map((r) => (
                <option key={r} value={r}>{msg.roles[r] ?? r}</option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <div className="flex gap-2">
        <Button variant="primary" loading={busy} onClick={() => void save()}>{msg.save}</Button>
        <Button variant="ghost" onClick={onDone}>{msg.cancel}</Button>
      </div>
    </section>
  );
}

const RegisterPage: PageComponent = () => {
  const shell = useShell();
  const location = useLocation();
  const navigate = useNavigate();
  const [book, setBook] = useState("NIIF");
  const res = useResource<ReportResult>("/api/v1/assets/reports/register", { company_id: shell.companyId });
  const rows = useMemo(() => registerRows(res.data, book), [res.data, book]);
  const creating = location.pathname.endsWith("/nuevo");
  if (creating) return <NewAssetForm onDone={() => { void navigate("/m/activos/registro"); res.reload(); }} />;
  const columns: GridColumn<RegisterRow>[] = [
    { id: "code", header: msg.code },
    { id: "name", header: msg.name },
    { id: "category", header: msg.category },
    { id: "status", header: msg.status, render: (r) => msg.lifecycle[r.status] ?? r.status },
    { id: "cost", header: msg.cost, type: "COP", total: true },
    { id: "accumulated_depreciation", header: msg.accumulated, type: "COP", total: true },
    { id: "accumulated_impairment", header: msg.impairment, type: "COP", total: true },
    { id: "net_book_value", header: msg.nbv, type: "COP", total: true },
  ];
  const labels = labelItems(res.data);
  return (
    <div className="flex flex-col gap-4" data-nx-page="activos.registro">
      <div className="flex flex-wrap items-end gap-3">
        <Field label={msg.book}>
          {(p) => (
            <Select id={p.id} value={book} onChange={(e) => setBook(e.target.value)}>
              <option value="NIIF">NIIF</option>
              <option value="FISCAL">{msg.fiscal}</option>
            </Select>
          )}
        </Field>
        <Button variant="secondary" disabled={!labels.length} onClick={() => downloadBlob(labelsBlob(labels), "etiquetas-activos.pdf")}>
          {msg.qrLabels}
        </Button>
        <Button variant="primary" onClick={() => void navigate("/m/activos/registro/nuevo")}>{msg.newAsset}</Button>
      </div>
      {res.loading ? <Skeleton className="h-64 w-full" label={msg.loading} /> : null}
      {res.error ? (
        <EmptyState title={msg.loadError} action={<Button onClick={res.reload}>{msg.retry}</Button>} />
      ) : !res.loading && rows.length === 0 ? (
        <EmptyState title={msg.emptyTitle} body={msg.emptyBody} action={<Button variant="primary" onClick={() => void navigate("/m/activos/registro/nuevo")}>{msg.newAsset}</Button>} />
      ) : (
        <DataGrid label={msg.registerTitle} resource="assets.asset" rows={rows} columns={columns} masks={shell.masks} userId={shell.userId} />
      )}
      {res.data?.synthetic ? <p className="text-xs text-[var(--nx-color-text-muted)]">{msg.synthetic}</p> : null}
    </div>
  );
};

export default RegisterPage;
