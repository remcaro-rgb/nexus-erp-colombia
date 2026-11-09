// Tooltip (owner P08): shown on hover AND keyboard focus (UI-CONTRACT §15); Radix handles Esc dismissal.
import * as RT from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export function TooltipProvider({ children }: { children: ReactNode }) {
  return <RT.Provider delayDuration={300}>{children}</RT.Provider>;
}

export function Tooltip({ content, children, side = "right" }: { content: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom" | "left" }) {
  return (
    <RT.Root>
      <RT.Trigger asChild>{children}</RT.Trigger>
      <RT.Portal>
        <RT.Content side={side} sideOffset={6} className="z-[50] max-w-xs rounded-control bg-slate-900 px-2 py-1 text-xs text-white shadow">
          {content}
        </RT.Content>
      </RT.Portal>
    </RT.Root>
  );
}
