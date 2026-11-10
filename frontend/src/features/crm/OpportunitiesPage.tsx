// Ventas › Oportunidades (CRM): pipeline board by stage with value and weighted value per column (masked when the user
// lacks the confidential field grant), win / lose with reason.
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, createApiClient } from "@lib/api/client";
import { can } from "@lib/permissions";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { formatCOP } from "@lib/money";
import { useOptionalShell } from "@shell/ShellContext";
import { CrmApi, type OpportunityDto, type PipelineDto } from "./api";
import { board, loseErrors, weightedPipeline } from "./model";

export default function OpportunitiesPage() {
  const shell = useOptionalShell();
  const companyId = shell?.companyId ?? null;
  const client = shell?.api;
  const api = useMemo(() => new CrmApi(client ?? createApiClient(), companyId), [client, companyId]);
  const mayWrite = shell ? can(shell.perms, "crm.opportunity.update") : false;
  const [pipeline, setPipeline] = useState<PipelineDto | null | undefined>(undefined);
  const [rows, setRows] = useState<OpportunityDto[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const pipes = await api.pipelines();
      const first = pipes.items[0] ?? null;
      setPipeline(first);
      setRows(first ? (await api.opportunities(first.id)).items : []);
    } catch (e) {
      setPipeline(null);
      setError(e instanceof ApiError ? e.message : "No fue posible cargar las oportunidades.");
    }
  }, [api]);
  useEffect(() => {
    void load();
  }, [load]);

  async function close(o: OpportunityDto, won: boolean) {
    const reason = won ? "" : (window.prompt("Motivo de la pérdida") ?? "");
    const errs = won ? [] : loseErrors(reason);
    if (errs.length) return setError(errs[0] ?? null);
    try {
      await api.close(o.id, won, reason);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No fue posible cerrar la oportunidad.");
    }
  }

  if (pipeline === undefined) return <Skeleton className="h-32 w-full" label="Cargando" />;
  if (!pipeline) return <EmptyState title="Sin embudo de ventas" body={error ?? "Cree un embudo con sus etapas para registrar oportunidades."} />;
  const columns = board(pipeline, rows);
  const weighted = weightedPipeline(rows);
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm">
        {pipeline.name} · pipeline ponderado {weighted == null ? "—" : formatCOP(weighted)}
      </p>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      <div className="grid auto-cols-[minmax(220px,1fr)] grid-flow-col gap-2 overflow-x-auto" aria-label="Oportunidades por etapa">
        {columns.map((col) => (
          <section key={col.stage} className="nx-card flex flex-col gap-2 p-2" aria-label={col.name}>
            <header className="text-xs text-muted">
              {col.name} · {col.probability} % · {col.total == null ? "—" : formatCOP(col.total)}
            </header>
            {col.items.map((o) => (
              <article key={o.id} className="rounded border p-2 text-sm">
                <p className="font-medium">{o.name}</p>
                <p className="tabular-nums">{o.amount ? formatCOP(o.amount.amount) : "—"}</p>
                <p className="text-xs text-muted">Cierre {o.close_date ?? "sin fecha"}</p>
                {mayWrite && (
                  <div className="mt-1 flex gap-1">
                    <Button onClick={() => void close(o, true)}>Ganada</Button>
                    <Button variant="ghost" onClick={() => void close(o, false)}>
                      Perdida
                    </Button>
                  </div>
                )}
              </article>
            ))}
            {!col.items.length && <Badge>Sin oportunidades</Badge>}
          </section>
        ))}
      </div>
    </div>
  );
}
