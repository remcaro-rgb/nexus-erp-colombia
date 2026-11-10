/** Containers of Facturación electrónica: data through the public API, company-scoped, stale responses dropped. */
import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router";
import { can } from "@lib/permissions";
import { useShell } from "@shell/ShellContext";
import type { PageComponent } from "@shell/registry";
import { einvoiceApi } from "./api";
import { ContingencyBanner, DocumentsTable, ProvidersPanel, RangesTable, type ProviderDraft } from "./components";
import { statusFilterFromQuery, type ContingencyPanel, type ContractReport, type FiscalDocument, type NumberingRange, type TechnologyProvider } from "./model";

function useEinvoice() {
  const shell = useShell();
  const companyId = shell.companyId ?? "";
  const api = useMemo(() => einvoiceApi(shell.api, companyId), [shell.api, companyId]);
  return { shell, companyId, api };
}

function errorText(e: unknown): string {
  const err = e as { code?: string; message?: string };
  return err?.code ? `${err.code}: ${err.message ?? ""}` : String(e);
}

function DocumentsView({ rejectionsOnly }: { rejectionsOnly: boolean }) {
  const { shell, companyId, api } = useEinvoice();
  const location = useLocation();
  const estado = statusFilterFromQuery(location.search);
  const [docs, setDocs] = useState<FiscalDocument[]>([]);
  const [contingency, setContingency] = useState<ContingencyPanel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!companyId || shell.switching) return;
    const controller = new AbortController();
    setDocs([]);
    const filters = rejectionsOnly ? [{ estado: "rejected" }, { estado: "error_retryable" }, { estado: "contingency_issued" }] : [{ estado: estado ?? undefined }];
    Promise.all(filters.map((f) => api.documents(f, controller.signal)))
      .then((pages) => !controller.signal.aborted && setDocs(pages.flatMap((p) => p.items)))
      .catch((e: unknown) => !controller.signal.aborted && setError(errorText(e)));
    if (rejectionsOnly && can(shell.perms, "einvoice.contingency.read")) {
      api.contingency(controller.signal).then((r) => !controller.signal.aborted && setContingency(r.contingency ?? null)).catch(() => undefined);
    }
    return () => controller.abort(); // company switch: never render the previous company's rows
  }, [api, companyId, estado, rejectionsOnly, nonce, shell.switching, shell.perms]);

  const act = (p: Promise<unknown>) => p.then(() => setNonce((n) => n + 1)).catch((e: unknown) => setError(errorText(e)));
  return (
    <section className="flex flex-col gap-3" aria-labelledby="fe-docs-title">
      <h1 id="fe-docs-title" className="text-lg font-semibold">{rejectionsOnly ? "Rechazos y contingencias" : "Documentos electrónicos"}</h1>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {rejectionsOnly && (
        <ContingencyBanner
          panel={contingency}
          canAdmin={can(shell.perms, "einvoice.contingency.admin")}
          onDeclare={(reason) => act(api.declareContingency(reason))}
          onEnd={() => act(api.endContingency())}
        />
      )}
      <DocumentsTable
        docs={docs}
        caption={rejectionsOnly ? "Documentos rechazados o pendientes" : "Documentos electrónicos"}
        canUpdate={can(shell.perms, "einvoice.document.update")}
        canExport={can(shell.perms, "einvoice.document.export")}
        onRetry={(id) => act(api.retry(id, "Reintento manual desde la consola"))}
        onResend={(id) => act(api.resendEmail(id))}
        xmlHref={api.xmlUrl}
      />
    </section>
  );
}

export const DocumentsPage: PageComponent = () => <DocumentsView rejectionsOnly={false} />;
export const RejectionsPage: PageComponent = () => <DocumentsView rejectionsOnly />;

export const NumberingPage: PageComponent = () => {
  const { shell, companyId, api } = useEinvoice();
  const [ranges, setRanges] = useState<NumberingRange[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!companyId || shell.switching) return;
    const controller = new AbortController();
    setRanges([]);
    api.ranges(controller.signal).then((p) => !controller.signal.aborted && setRanges(p.items)).catch((e: unknown) => !controller.signal.aborted && setError(errorText(e)));
    return () => controller.abort();
  }, [api, companyId, shell.switching]);
  return (
    <section className="flex flex-col gap-3" aria-labelledby="fe-num-title">
      <h1 id="fe-num-title" className="text-lg font-semibold">Numeración autorizada</h1>
      <p className="text-sm">Los rangos y la clave técnica provienen de la resolución DIAN (dato externo). En DEV se usan rangos sintéticos.</p>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <RangesTable ranges={ranges} />
    </section>
  );
};

export const ProvidersPage: PageComponent = () => {
  const { shell, companyId, api } = useEinvoice();
  const [providers, setProviders] = useState<TechnologyProvider[]>([]);
  const [reports, setReports] = useState<Record<string, ContractReport | undefined>>({});
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!companyId || shell.switching) return;
    const controller = new AbortController();
    setProviders([]);
    setReports({});
    api.providers(controller.signal).then((p) => !controller.signal.aborted && setProviders(p.items)).catch((e: unknown) => !controller.signal.aborted && setError(errorText(e)));
    return () => controller.abort();
  }, [api, companyId, nonce, shell.switching]);
  const create = (d: ProviderDraft) =>
    api
      .createProvider({ code: d.code, name: d.name, adapter: d.adapter, base_url: d.base_url, environment: "SANDBOX", ...(d.credentials_ref ? { credentials_ref: d.credentials_ref } : {}) })
      .then(() => setNonce((n) => n + 1))
      .catch((e: unknown) => setError(errorText(e)));
  const test = (id: string) =>
    api
      .testConnection(id)
      .then((r) => r.data && setReports((prev) => ({ ...prev, [id]: r.data?.report })))
      .catch((e: unknown) => {
        const problem = (e as { problem?: { errors?: { cases?: ContractReport["cases"] } } }).problem;
        if (problem?.errors?.cases) setReports((prev) => ({ ...prev, [id]: { adapter: "", suite_version: "", passed: false, cases: problem.errors?.cases ?? [] } }));
        setError(errorText(e));
      });
  return (
    <>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <ProvidersPanel
        providers={providers}
        reports={reports}
        canCreate={can(shell.perms, "einvoice.provider.create")}
        canAdmin={can(shell.perms, "einvoice.provider.admin")}
        canExport={can(shell.perms, "einvoice.provider.export")}
        exportHref={api.exportUrl()}
        onCreate={create}
        onTest={test}
      />
    </>
  );
};
