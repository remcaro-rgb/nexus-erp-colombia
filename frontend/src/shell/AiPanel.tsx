// AI panel FRAME (owner P08, UI-CONTRACT §8). Content is P19's (features/ai_agent) mounted through the "ai-panel" slot.
// ≥ 1280: docked 320 px (pushes the center); 768–1279: overlay drawer with scrim (focus trapped, Esc closes, focus
// returns to the toggle); < 768: full-width sheet. Without P19 the frame says so honestly — no canned replies.
import { Sparkles, X } from "lucide-react";
import { useLocation } from "react-router";
import * as RD from "@radix-ui/react-dialog";
import { useT } from "@i18n/index";
import { cn } from "@lib/cn";
import { getSlot, pageContextFrom, peekAiPrompt, takeAiPrompt, useSlotsVersion, type AiPanelSlotProps } from "./slots";
import { AI_PANEL_ID } from "./Header";
import { useShell } from "./ShellContext";
import type { Breakpoint } from "./useBreakpoint";

function PanelBody({ onClose, moduleId, closeAsDialog }: { onClose: () => void; moduleId: string | null; closeAsDialog: boolean }) {
  const t = useT();
  const loc = useLocation();
  useSlotsVersion();
  const shell = useShell();
  const slot = getSlot("ai-panel");
  const Content = slot && (!slot.permission || shell.perms.has(slot.permission)) ? (slot.component as React.ComponentType<AiPanelSlotProps>) : null;
  const title = <span className="text-sm font-bold text-fg">{t("ai.title")}</span>;
  const closeBtn = (
    <button type="button" onClick={onClose} aria-label={t("ai.close")} className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-control text-secondary hover:bg-surface">
      <X aria-hidden="true" size={16} />
    </button>
  );
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 shrink-0 items-center justify-between gap-2 border-b border-line bg-indigo-50 px-4 dark:bg-indigo-950/20">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className="inline-flex h-8 w-8 items-center justify-center rounded-[12px] bg-brand text-white dark:text-slate-950">
            <Sparkles size={16} />
          </span>
          <div>
            {closeAsDialog ? <RD.Title asChild>{title}</RD.Title> : <h2>{title}</h2>}
            <p className="text-xs text-muted">{t("ai.subtitle")}</p>
          </div>
        </div>
        {closeBtn}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {Content ? (
          <Content pageContext={pageContextFrom(loc.pathname, moduleId)} pendingPrompt={peekAiPrompt()} consumePrompt={() => void takeAiPrompt()} />
        ) : (
          <p className="p-4 text-sm text-muted" role="status">
            {t("ai.unavailable")}
          </p>
        )}
      </div>
    </div>
  );
}

export function AiPanel({ bp, moduleId }: { bp: Breakpoint; moduleId: string | null }) {
  const shell = useShell();
  const t = useT();
  const close = () => {
    shell.setAiOpen(false);
    window.setTimeout(() => document.querySelector<HTMLElement>('[data-nx-control="ai-toggle"]')?.focus(), 0);
  };
  if (bp === "xl") {
    if (!shell.aiOpen) return null;
    return (
      <aside id={AI_PANEL_ID} aria-label={t("ai.title")} data-nx-region="ai-panel" className="relative z-[20] h-full w-[var(--nx-ai-panel-w)] shrink-0 border-l border-line bg-surface">
        <PanelBody onClose={close} moduleId={moduleId} closeAsDialog={false} />
      </aside>
    );
  }
  return (
    <RD.Root open={shell.aiOpen} onOpenChange={(o) => (o ? shell.setAiOpen(true) : close())}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-[40] bg-slate-950/40" />
        <RD.Content
          id={AI_PANEL_ID}
          data-nx-region="ai-panel"
          aria-describedby={undefined}
          className={cn("fixed right-0 top-0 z-[40] h-full border-l border-line bg-surface shadow-xl", bp === "sm" ? "w-full" : "w-[var(--nx-ai-panel-w)]")}
        >
          <PanelBody onClose={close} moduleId={moduleId} closeAsDialog />
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
