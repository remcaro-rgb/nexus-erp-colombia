// Reportes › Programados (P17a): saved reports visible to the user — run (current permissions), publish, schedule
// (P07 scheduler, owner's current permissions at each run) and archive. Native labelled controls only.
import { useEffect, useState } from "react";
import { Badge } from "@ds/components/Badge";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { ApiError } from "@lib/api/client";
import type { ReportResult, SavedReportDto } from "../api";
import { ResultTable } from "../ResultTable";
import { useReportingApi } from "../useReportingApi";

export default function SavedReportsPage() {
  const { api, companyId, can } = useReportingApi();
  const [rows, setRows] = useState<SavedReportDto[] | null>(null);
  const [result, setResult] = useState<{ name: string; data: ReportResult } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setRows(null);
    api.reports().then(
      (p) => live && setRows(p.items),
      () => live && setRows([]),
    );
    return () => {
      live = false;
    };
  }, [api, companyId, tick]);

  const act = async (r: SavedReportDto, action: "run" | "publish" | "archive" | "schedule") => {
    setError(null);
    try {
      if (action === "schedule") {
        const cron = window.prompt("Programación (cron de 5 campos, hora de Bogotá)", r.schedule ?? "30 7 * * 1");
        if (!cron) return;
        await api.act(r.id, "schedule", { cron, format: "pdf" });
      } else if (action === "run") {
        setResult({ name: r.name, data: await api.act(r.id, "run") });
      } else {
        await api.act(r.id, action);
      }
      setTick((n) => n + 1);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.code}: ${e.message}` : "Acción no disponible");
    }
  };

  if (!rows) return <Skeleton className="h-32 w-full" label="Cargando reportes" />;
  return (
    <section aria-label="Reportes guardados" className="space-y-4">
      {!rows.length ? (
        <EmptyState title="Aún no hay reportes guardados" body="Cree uno desde el Explorador y guárdelo como reporte." />
      ) : (
        <table className="nx-card w-full text-sm" aria-label="Reportes guardados">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th scope="col">Código</th>
              <th scope="col">Nombre</th>
              <th scope="col">Estado</th>
              <th scope="col">Programación</th>
              <th scope="col">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.code}</td>
                <td>{r.name}</td>
                <td>
                  <Badge tone={r.status === "ARCHIVED" ? "neutral" : r.definition.published ? "positive" : "brand"}>
                    {r.status === "ARCHIVED" ? "Archivado" : r.definition.published ? "Publicado" : "Borrador"}
                  </Badge>
                </td>
                <td>{r.schedule ?? "—"}</td>
                <td className="flex flex-wrap gap-1">
                  <Button onClick={() => act(r, "run")} disabled={r.status !== "ACTIVE" || !can("reporting.report.update")} aria-label={`Ejecutar ${r.name}`}>
                    Ejecutar
                  </Button>
                  <Button onClick={() => act(r, "publish")} disabled={r.status !== "ACTIVE" || !!r.definition.published} aria-label={`Publicar ${r.name}`}>
                    Publicar
                  </Button>
                  <Button onClick={() => act(r, "schedule")} disabled={r.status !== "ACTIVE"} aria-label={`Programar ${r.name}`}>
                    Programar
                  </Button>
                  <Button variant="danger" onClick={() => act(r, "archive")} disabled={r.status !== "ACTIVE"} aria-label={`Archivar ${r.name}`}>
                    Archivar
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
      {result ? <ResultTable result={result.data} caption={`Ejecución de ${result.name}`} /> : null}
    </section>
  );
}
