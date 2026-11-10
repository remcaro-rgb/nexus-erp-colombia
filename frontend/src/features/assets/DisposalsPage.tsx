// "Bajas y traslados" (/m/activos/bajas), owner P14: :dispose (sale / scrap / donation, reason required) and
// :transfer (location; dimension changes reclassify the GL per book). Success only after the API answers.
import { useMemo, useState } from "react";
import { ApiError } from "@lib/api/client";
import { Button } from "@ds/components/Button";
import { EmptyState } from "@ds/components/Feedback";
import { Field, Input, Select } from "@ds/components/Field";
import { useToast } from "@ds/components/Toast";
import { useResource, useShell } from "@shell/ShellContext";
import type { PageComponent } from "@shell/registry";
import { assetsApi, labelItems, problemText, type ReportResult } from "./api";
import { msg } from "./messages";

interface AssetRow {
  id: string;
  code: string;
}

const DisposalsPage: PageComponent = () => {
  const shell = useShell();
  const toast = useToast();
  const res = useResource<ReportResult>("/api/v1/assets/reports/register", { company_id: shell.companyId });
  const assets = useMemo<AssetRow[]>(() => {
    const codes = new Set(labelItems(res.data).map((a) => a.code));
    const seen = new Map<string, AssetRow>();
    for (const r of res.data?.rows ?? []) if (codes.has(String(r.code))) seen.set(String(r.asset_id), { id: String(r.asset_id), code: String(r.code) });
    return [...seen.values()];
  }, [res.data]);
  const [assetId, setAssetId] = useState("");
  const [f, setF] = useState({ kind: "SCRAP", date: "", proceeds: "", vat: "", customer_id: "", reason: "", location: "" });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((s) => ({ ...s, [k]: e.target.value }));
  const client = assetsApi(shell.api, shell.companyId);
  const code = assets.find((a) => a.id === assetId)?.code ?? "";

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try {
      await fn();
      toast.push({ tone: "success", title: ok });
      res.reload();
    } catch (e) {
      if (e instanceof ApiError) toast.push({ tone: "error", title: problemText(e.code, e.message), traceId: e.traceId });
      else throw e;
    } finally {
      setBusy(false);
    }
  }

  if (!res.loading && !assets.length) return <EmptyState title={msg.emptyTitle} body={msg.emptyBody} />;
  const sale = f.kind === "SALE";
  return (
    <div className="grid max-w-3xl grid-cols-1 gap-4 sm:grid-cols-2" data-nx-page="activos.bajas">
      <Field label={msg.asset} required>
        {(p) => (
          <Select id={p.id} value={assetId} onChange={(e) => setAssetId(e.target.value)}>
            <option value="">{msg.choose}</option>
            {assets.map((a) => (
              <option key={a.id} value={a.id}>{a.code}</option>
            ))}
          </Select>
        )}
      </Field>
      <Field label={msg.date}>{(p) => <Input id={p.id} type="date" value={f.date} onChange={set("date")} />}</Field>
      <Field label={msg.reason} required>{(p) => <Input id={p.id} value={f.reason} onChange={set("reason")} maxLength={2000} />}</Field>
      <Field label={msg.kind}>
        {(p) => (
          <Select id={p.id} value={f.kind} onChange={set("kind")}>
            {Object.entries(msg.kinds).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </Select>
        )}
      </Field>
      {sale ? (
        <>
          <Field label={msg.proceeds} hint="COP">{(p) => <Input id={p.id} inputMode="decimal" value={f.proceeds} onChange={set("proceeds")} />}</Field>
          <Field label={msg.vat} hint="COP">{(p) => <Input id={p.id} inputMode="decimal" value={f.vat} onChange={set("vat")} />}</Field>
          <Field label={msg.customer}>{(p) => <Input id={p.id} value={f.customer_id} onChange={set("customer_id")} />}</Field>
        </>
      ) : null}
      <div className="flex items-end gap-2 sm:col-span-2">
        <Button
          variant="danger"
          loading={busy}
          disabled={!assetId || !f.reason.trim()}
          onClick={() =>
            void run(
              () => client.dispose(assetId, f.reason, f.date, { kind: f.kind, ...(sale ? { proceeds: f.proceeds || "0", vat: f.vat || "0", customer_id: f.customer_id } : {}) }),
              msg.disposed(code),
            )
          }
        >
          {msg.dispose}
        </Button>
      </div>
      <Field label={msg.newLocation}>{(p) => <Input id={p.id} value={f.location} onChange={set("location")} maxLength={255} />}</Field>
      <div className="flex items-end">
        <Button variant="secondary" loading={busy} disabled={!assetId || !f.location.trim()} onClick={() => void run(() => client.transfer(assetId, f.reason, f.date, { location: f.location }), msg.transferred(code))}>
          {msg.transfer}
        </Button>
      </div>
    </div>
  );
};

export default DisposalsPage;
