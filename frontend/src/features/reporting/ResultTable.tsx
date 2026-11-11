// Accessible result table of a semantic query (header cells with scope, numeric cells right-aligned, totals row,
// k-anonymity suppression and truncation notices as text — colour is never the only signal).
import type { ReportResult } from "./api";
import { formatCell, isNumeric, totalsLine } from "./model";

export function ResultTable({ result, caption }: { result: ReportResult; caption: string }) {
  const totals = totalsLine(result);
  return (
    <div className="space-y-2">
      {result.warnings.map((w) => (
        <p key={w.code} role="status" className="text-sm text-muted">
          {w.detail}
        </p>
      ))}
      {result.truncated ? <p role="status" className="text-sm">Resultado truncado: aumente el límite o use una exportación.</p> : null}
      <table className="nx-card w-full text-sm" aria-label={caption}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {result.columns.map((c) => (
              <th key={c.key} scope="col" className={isNumeric(c) ? "text-right" : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, n) => (
            <tr key={n}>
              {result.columns.map((c) => (
                <td key={c.key} className={isNumeric(c) ? "text-right tabular-nums" : undefined}>
                  {formatCell(c, row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {totals.length ? (
        <p className="text-sm font-semibold" aria-label="Totales">
          {totals.map((t) => `${t.label}: ${t.value}`).join(" · ")}
        </p>
      ) : null}
      {result.synthetic ? <p className="text-xs text-muted">Datos sintéticos (DEV).</p> : null}
    </div>
  );
}
