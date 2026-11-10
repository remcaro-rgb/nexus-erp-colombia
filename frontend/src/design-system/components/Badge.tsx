// Badge (owner P08): posting / fiscal / node-type status. Colour is never the only signal: the text is always shown.
import type { ReactNode } from "react";
import { cn } from "@lib/cn";
import { useT, type MessageKey } from "@i18n/index";

export type BadgeTone = "neutral" | "brand" | "positive" | "warning" | "danger";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-nav-hover text-secondary border-line",
  brand: "bg-brand-soft text-brand border-brand-soft-line",
  positive: "bg-positive-bg text-positive border-transparent",
  warning: "bg-warning-bg text-warning border-transparent",
  danger: "bg-danger-bg text-danger border-transparent",
};

export function Badge({ tone = "neutral", children, className }: { tone?: BadgeTone; children: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold", TONES[tone], className)}>
      {children}
    </span>
  );
}

export type PostingStatus = "draft" | "pending" | "approved" | "posted" | "reversed" | "cancelled";
export type FiscalStatus = "pending" | "accepted" | "rejected" | "simulated";

const POSTING: Record<PostingStatus, [MessageKey, BadgeTone]> = {
  draft: ["status.draft", "neutral"],
  pending: ["status.pending", "warning"],
  approved: ["status.approved", "brand"],
  posted: ["status.posted", "positive"],
  reversed: ["status.reversed", "danger"],
  cancelled: ["status.cancelled", "danger"],
};

const FISCAL: Record<FiscalStatus, [MessageKey, BadgeTone]> = {
  pending: ["status.fiscal.pending", "warning"],
  accepted: ["status.fiscal.accepted", "positive"],
  rejected: ["status.fiscal.rejected", "danger"],
  simulated: ["status.fiscal.simulated", "neutral"],
};

export function PostingBadge({ status }: { status: PostingStatus }) {
  const t = useT();
  const [k, tone] = POSTING[status];
  return <Badge tone={tone}>{t(k)}</Badge>;
}

export function FiscalBadge({ status }: { status: FiscalStatus }) {
  const t = useT();
  const [k, tone] = FISCAL[status];
  return <Badge tone={tone}>{t(k)}</Badge>;
}
