/**
 * Talento › Nómina › Conceptos y fórmulas (F-PAY-FORMULA): concept list, dependency view in evaluation
 * order (D-025), graph validation against the server (`:validate-graph`, the only authority) with the
 * legal parameters the graph reads and their labels. Formulas themselves are authored in the shared
 * formula editor (P08) through the workflow formulas API (domain payroll); the UI is not a security
 * boundary: the API enforces every permission.
 */

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@ds/components/Badge";

import { PayrollApiError, payrollApi, type PayrollApiContext } from "./api";
import { CONCEPT_TYPE_LABEL, dependencyOrder, legalLabel, type GraphValidation, type PayrollConcept } from "./model";

interface Props {
  companyId: string;
  /** Reference date of the selected payroll period (ISO yyyy-mm-dd). */
  onDate: string;
  fetchImpl?: typeof fetch;
}

export function ConceptsPage({ companyId, onDate, fetchImpl }: Props) {
  const [concepts, setConcepts] = useState<PayrollConcept[]>([]);
  const [graph, setGraph] = useState<GraphValidation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const ctx: PayrollApiContext = { companyId, signal: controller.signal, fetchImpl };
    Promise.all([payrollApi.listConcepts(ctx), payrollApi.validateGraph(ctx, onDate)])
      .then(([page, validation]) => {
        setConcepts(page.items);
        setGraph(validation);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setError(e instanceof PayrollApiError ? `${e.code}: ${e.message}` : String(e));
      });
    return () => controller.abort(); // company switch: discard the previous company's response
  }, [companyId, onDate, fetchImpl]);

  const local = useMemo(() => dependencyOrder(concepts), [concepts]);
  const byCode = useMemo(() => new Map(concepts.map((c) => [c.code, c])), [concepts]);
  const order = graph?.data.order.length ? graph.data.order : (local.order ?? []);

  return (
    <section aria-labelledby="payroll-concepts-title" className="flex flex-col gap-4">
      <h1 id="payroll-concepts-title" className="text-lg font-semibold">
        Conceptos y fórmulas de nómina
      </h1>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      {graph?.status === "invalid" && (
        <div role="alert" aria-label="Errores del grafo de conceptos" className="rounded-xl border p-3 text-sm text-danger">
          {graph.data.errors.map((e) => (
            <p key={e.code}>
              {e.code}: {e.detail}
            </p>
          ))}
        </div>
      )}
      {local.cycle.length > 0 && (
        <p role="alert" className="text-sm text-danger">
          Ciclo de dependencias: {local.cycle.join(" → ")}
        </p>
      )}
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Conceptos en orden de evaluación</caption>
        <thead>
          <tr>
            <th scope="col">Orden</th>
            <th scope="col">Código</th>
            <th scope="col">Nombre</th>
            <th scope="col">Tipo</th>
            <th scope="col">Depende de</th>
            <th scope="col">Fórmula</th>
          </tr>
        </thead>
        <tbody>
          {order
            .map((code) => byCode.get(code))
            .filter((c): c is PayrollConcept => c !== undefined)
            .map((c, i) => (
              <tr key={c.id} data-concept={c.code}>
                <td>{i + 1}</td>
                <td>{c.code}</td>
                <td>{c.name}</td>
                <td>{CONCEPT_TYPE_LABEL[c.concept_type]}</td>
                <td>{c.depends_on.join(", ") || "—"}</td>
                <td>{c.formula_id ? "Fórmula" : "Novedad"}</td>
              </tr>
            ))}
        </tbody>
      </table>
      {graph && graph.data.parameters.length > 0 && (
        <section aria-labelledby="payroll-params-title">
          <h2 id="payroll-params-title" className="text-base font-semibold">
            Parámetros legales leídos ({graph.data.on})
          </h2>
          <ul>
            {graph.data.parameters.map((p) => (
              <li key={p.code} data-parameter={p.code}>
                {p.input} → {p.code} [{p.status}] {legalLabel(p) && <Badge tone="warning">{legalLabel(p)}</Badge>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}
