// Pure helpers of the CRM screens (no I/O; unit-tested). Decimal strings only.
import { sumDecimals } from "@lib/money";
import type { OpportunityDto, PipelineDto } from "./api";

export interface BoardColumn {
  stage: string;
  name: string;
  probability: string;
  items: OpportunityDto[];
  /** Σ amount of the visible cards (null when amounts are masked for the user). */
  total: string | null;
  weighted: string | null;
}

/** Kanban columns in pipeline order with the OPEN opportunities of each stage; unknown stages go last. */
export function board(pipeline: PipelineDto, rows: OpportunityDto[]): BoardColumn[] {
  const open = rows.filter((o) => o.opportunity_status === "OPEN" && o.pipeline_id === pipeline.id);
  const masked = open.some((o) => o.amount == null);
  const cols: BoardColumn[] = pipeline.stages.map((s) => {
    const items = open.filter((o) => o.stage === s.code);
    return {
      stage: s.code,
      name: s.name,
      probability: s.probability,
      items,
      total: masked ? null : sumDecimals(items.map((o) => o.amount?.amount)),
      weighted: masked ? null : sumDecimals(items.map((o) => o.weighted_amount?.amount)),
    };
  });
  const known = new Set(pipeline.stages.map((s) => s.code));
  const orphans = open.filter((o) => !known.has(o.stage));
  if (orphans.length) cols.push({ stage: "?", name: "Sin etapa", probability: "0", items: orphans, total: null, weighted: null });
  return cols;
}

/** Weighted pipeline KPI (Σ value × probability of OPEN opportunities). */
export function weightedPipeline(rows: OpportunityDto[]): string | null {
  const open = rows.filter((o) => o.opportunity_status === "OPEN");
  if (open.some((o) => o.weighted_amount == null)) return null;
  return sumDecimals(open.map((o) => o.weighted_amount?.amount));
}

/** Losing an opportunity requires a reason (server error reason_required). */
export function loseErrors(reason: string): string[] {
  return reason.trim() ? [] : ["Indique el motivo de la pérdida."];
}
