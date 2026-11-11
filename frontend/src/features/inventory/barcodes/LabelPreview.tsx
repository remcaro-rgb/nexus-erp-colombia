// Label print preview (D-005): bars drawn as SVG from the server's module string, fixed text fields (never cost).
// Printing uses the browser's print dialog with the label size (no printer driver in dev).
import type { LabelDto } from "./api";
import { bars, labelLines } from "./model";

export default function LabelPreview({ label, fields, widthMm = 50, heightMm = 25 }: { label: LabelDto; fields: readonly string[]; widthMm?: number; heightMm?: number }) {
  const quiet = 10;
  const total = label.modules.length + quiet * 2;
  const lines = labelLines(label.text, fields);
  return (
    <figure className="nx-label-preview inline-flex flex-col items-center border border-border bg-white p-1 text-black" style={{ width: `${widthMm}mm`, minHeight: `${heightMm}mm` }}>
      <svg viewBox={`0 0 ${total} 40`} preserveAspectRatio="none" width="100%" height="55%" role="img" aria-label={`Código ${label.symbology} ${label.value}`}>
        <rect x={0} y={0} width={total} height={40} fill="#fff" />
        {bars(label.modules, quiet).map((b) => (
          <rect key={b.x} x={b.x} y={0} width={b.width} height={40} fill="#000" />
        ))}
      </svg>
      <figcaption className="text-center text-[8px] leading-tight">
        {lines.map((l) => (
          <div key={l}>{l}</div>
        ))}
      </figcaption>
    </figure>
  );
}
