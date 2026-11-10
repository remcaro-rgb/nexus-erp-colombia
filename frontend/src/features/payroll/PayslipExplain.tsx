/**
 * Explain view of one payslip (Talento › Nómina, F-PAY-FORMULA): every line with its formula version,
 * inputs, explain trace and the legal parameters it read. Fixture values carry the label
 * "NOT LEGALLY VERIFIED"; masked fields (no payroll field grant) are shown as "•••", never as numbers.
 */

import { Badge } from "@ds/components/Badge";

import { CONCEPT_TYPE_LABEL, NOT_LEGALLY_VERIFIED, formatMoney, legalLabel, payslipNeedsLabel, type Payslip } from "./model";

export function PayslipExplain({ payslip }: { payslip: Payslip }) {
  const labelled = payslipNeedsLabel(payslip);
  return (
    <section aria-labelledby={`payslip-${payslip.id}`} className="flex flex-col gap-3 text-sm">
      <header className="flex flex-wrap items-center gap-2">
        <h2 id={`payslip-${payslip.id}`} className="text-base font-semibold">
          Desprendible de nómina {payslip.employee_name ?? "•••"}
        </h2>
        {labelled && (
          <span role="status" aria-label="Estado legal del desprendible" data-legal-label="payslip">
            <Badge tone="warning">{NOT_LEGALLY_VERIFIED}</Badge>
          </span>
        )}
      </header>
      {labelled && (
        <p className="text-xs text-warning">
          Parámetros legales de prueba o sin revisión legal (EG-7): los valores no constituyen cumplimiento legal.
        </p>
      )}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
        <dt>Devengado</dt>
        <dd>{formatMoney(payslip.earnings)}</dd>
        <dt>Deducciones</dt>
        <dd>{formatMoney(payslip.deductions)}</dd>
        <dt>Neto a pagar</dt>
        <dd>{formatMoney(payslip.net_pay)}</dd>
        <dt>Costo empleador</dt>
        <dd>{formatMoney(payslip.employer_cost)}</dd>
      </dl>
      {payslip.lines === null ? (
        <p>Detalle restringido: requiere permiso de montos de nómina.</p>
      ) : (
        <table className="w-full text-left text-xs">
          <caption className="sr-only">Conceptos liquidados en orden de evaluación</caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Concepto</th>
              <th scope="col">Tipo</th>
              <th scope="col">Valor</th>
              <th scope="col">Explicación</th>
            </tr>
          </thead>
          <tbody>
            {payslip.lines.map((line) => (
              <tr key={line.concept}>
                <td>{line.sequence}</td>
                <td>
                  {line.concept}
                  {line.formula_version !== null && <span className="ml-1 text-secondary">v{line.formula_version}</span>}
                </td>
                <td>{CONCEPT_TYPE_LABEL[line.concept_type]}</td>
                <td>{formatMoney(line.amount)}</td>
                <td>
                  <ol aria-label={`Traza de ${line.concept}`} className="list-decimal pl-4">
                    {line.trace.map((t, i) => (
                      <li key={`${t.expr}-${i}`}>
                        <code>{t.expr}</code> = {t.value}
                      </li>
                    ))}
                  </ol>
                  {line.parameters.map((p) => (
                    <div key={p.code} data-parameter={p.code}>
                      {p.code} [{p.status}]{legalLabel(p) && <Badge tone="warning">{legalLabel(p)}</Badge>}
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
