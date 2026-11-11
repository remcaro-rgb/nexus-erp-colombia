// Reportes › Explorador (P17a report builder): pick a semantic entity, measures, dimensions (incl. hierarchy groups
// and time grains), filters and the as-of hierarchy date; run through the server's semantic layer (row/field policy),
// see the accessible table, download CSV, and save the definition as a report. Native labelled controls only.
import { useEffect, useMemo, useState } from "react";
import { Button } from "@ds/components/Button";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { ApiError } from "@lib/api/client";
import type { FilterOp, ReportResult, SemanticEntity } from "../api";
import { buildQuery, dimensionOptions, emptyBuilder, toCsv, type BuilderState } from "../model";
import { ResultTable } from "../ResultTable";
import { useReportingApi } from "../useReportingApi";

const OPS: Array<{ value: FilterOp; label: string }> = [
  { value: "eq", label: "es igual a" },
  { value: "ne", label: "es distinto de" },
  { value: "gte", label: "≥" },
  { value: "lte", label: "≤" },
  { value: "contains", label: "contiene" },
  { value: "in_subtree", label: "en el subárbol de (id)" },
  { value: "is_null", label: "está vacío" },
  { value: "not_null", label: "no está vacío" },
];

export default function ExplorerPage() {
  const { api, companyId, can } = useReportingApi();
  const [model, setModel] = useState<SemanticEntity[] | null>(null);
  const [state, setState] = useState<BuilderState>(emptyBuilder());
  const [result, setResult] = useState<ReportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    setModel(null);
    api.semanticModel().then(
      (m) => {
        if (!live) return;
        setModel(m);
        if (m[0]) setState(emptyBuilder(m[0].entity));
      },
      () => live && setModel([]),
    );
    return () => {
      live = false;
    };
  }, [api, companyId]);

  const entity = useMemo(() => model?.find((e) => e.entity === state.entity) ?? null, [model, state.entity]);
  if (!model) return <Skeleton className="h-40 w-full" label="Cargando modelo semántico" />;
  if (!model.length || !entity) return <EmptyState title="Sin datos disponibles" body="Su rol no tiene entidades de reporte habilitadas." />;

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.query(buildQuery(state)));
    } catch (e) {
      setResult(null);
      setError(e instanceof ApiError ? `${e.code}: ${e.message}` : "Error al ejecutar la consulta");
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    const name = window.prompt("Nombre del reporte");
    if (!name) return;
    const code = name.toUpperCase().replace(/[^A-Z0-9]+/g, "-").slice(0, 40) || "REPORTE";
    try {
      await api.createReport(code, name, buildQuery(state));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.code}: ${e.message}` : "No se pudo guardar");
    }
  };
  const download = () => {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([toCsv(result)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${result.report}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section aria-label="Explorador de reportes" className="space-y-4">
      <div className="nx-card grid gap-3 md:grid-cols-3">
        <label className="flex flex-col text-sm">
          Entidad
          <select value={state.entity} onChange={(e) => setState(emptyBuilder(e.target.value))}>
            {model.map((m) => (
              <option key={m.entity} value={m.entity}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="text-sm">
          <legend>Medidas</legend>
          {entity.measures.map((m) => (
            <label key={m.key} className="mr-3 inline-flex items-center gap-1">
              <input type="checkbox" checked={state.measures.includes(m.key)} onChange={() => setState({ ...state, measures: toggle(state.measures, m.key) })} />
              {m.label}
              {m.sensitive ? " (protegida, k ≥ 5)" : ""}
            </label>
          ))}
        </fieldset>
        <fieldset className="text-sm">
          <legend>Dimensiones</legend>
          {dimensionOptions(entity).map((d) => (
            <label key={d.value} className="mr-3 inline-flex items-center gap-1">
              <input type="checkbox" checked={state.dimensions.includes(d.value)} onChange={() => setState({ ...state, dimensions: toggle(state.dimensions, d.value) })} />
              {d.label}
            </label>
          ))}
        </fieldset>
        <label className="flex flex-col text-sm">
          Agrupación de fechas
          <select value={state.timeGrain} onChange={(e) => setState({ ...state, timeGrain: e.target.value })}>
            <option value="">Sin agrupar</option>
            {entity.time_grains.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          Jerarquía a la fecha (opcional)
          <input type="date" value={state.asOf} onChange={(e) => setState({ ...state, asOf: e.target.value })} />
        </label>
        <fieldset className="text-sm md:col-span-3">
          <legend>Filtros</legend>
          {state.filters.map((f, n) => (
            <div key={n} className="flex flex-wrap items-center gap-2">
              <label>
                Campo
                <select value={f.field} onChange={(e) => setState({ ...state, filters: state.filters.map((x, i) => (i === n ? { ...x, field: e.target.value } : x)) })}>
                  {entity.fields.map((x) => (
                    <option key={x.key} value={x.key}>
                      {x.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Condición
                <select value={f.op} onChange={(e) => setState({ ...state, filters: state.filters.map((x, i) => (i === n ? { ...x, op: e.target.value as FilterOp } : x)) })}>
                  {OPS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Valor
                <input
                  value={String(f.value ?? "")}
                  onChange={(e) =>
                    setState({
                      ...state,
                      filters: state.filters.map((x, i) => (i === n ? { ...x, value: x.op === "in_subtree" ? e.target.value.split(",").map((s) => s.trim()) : e.target.value } : x)),
                    })
                  }
                />
              </label>
              <Button variant="ghost" onClick={() => setState({ ...state, filters: state.filters.filter((_, i) => i !== n) })}>
                Quitar filtro
              </Button>
            </div>
          ))}
          <Button variant="ghost" onClick={() => setState({ ...state, filters: [...state.filters, { field: entity.fields[0]?.key ?? "", op: "eq", value: "" }] })}>
            Agregar filtro
          </Button>
        </fieldset>
        <div className="flex gap-2 md:col-span-3">
          <Button variant="primary" onClick={run} loading={busy} disabled={!state.measures.length}>
            Ejecutar
          </Button>
          {can("reporting.report.create") ? (
            <Button onClick={save} disabled={!state.measures.length}>
              Guardar como reporte
            </Button>
          ) : null}
          <Button onClick={download} disabled={!result}>
            Descargar CSV
          </Button>
        </div>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
      {result ? <ResultTable result={result} caption={`Resultado de ${entity.label}`} /> : null}
    </section>
  );
}
