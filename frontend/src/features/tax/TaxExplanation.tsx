/** "¿Por qué este impuesto?" popover content: the engine trace for one computed line (also used by the AI panel). */

import { explain, type TraceStep } from "./model";

export function TaxExplanation({ trace, lineRef, taxCode }: { trace: TraceStep[]; lineRef: string; taxCode: string }) {
  const steps = explain(trace, lineRef, taxCode);
  return (
    <div role="dialog" aria-label={`Explicación de ${taxCode} en la línea ${lineRef}`} className="max-w-md text-xs">
      <ol className="list-decimal pl-4">
        {steps.map((s, i) => (
          <li key={`${s.step}-${i}`}>
            <span className="font-semibold">{s.step}</span>: {s.detail}
            {typeof s.data.status === "string" && <span className="ml-1 text-slate-500">[{s.data.status}]</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}
