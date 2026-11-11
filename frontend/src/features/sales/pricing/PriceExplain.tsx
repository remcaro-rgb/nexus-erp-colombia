// Price explain popover of a document line (F-SAL-PRICING › UI): origin, list/formula binding, inputs the user may read,
// override and approval, FX and rounding. Built from the server snapshot, which is already field-masked.
import type { PriceSnapshot } from "../api";
import { explainRows } from "../model";

export function PriceExplain({ snapshot }: { snapshot: PriceSnapshot }) {
  const rows = explainRows(snapshot);
  if (!rows.length) return null;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted">¿De dónde sale este precio?</summary>
      <dl className="nx-card mt-1 grid grid-cols-2 gap-x-3 gap-y-1 p-2" aria-label="Explicación del precio">
        {rows.map((r) => (
          <div key={r.label} className="contents">
            <dt className="text-muted">{r.label}</dt>
            <dd className="tabular-nums">{r.value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
