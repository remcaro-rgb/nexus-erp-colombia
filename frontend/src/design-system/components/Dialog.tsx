// Dialog and Drawer (owner P08) on Radix primitives: focus trapped, Esc closes, focus returns to the trigger.
import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@lib/cn";
import { useT } from "@i18n/index";

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  /** Drawer side; omit for a centred dialog. */
  side?: "left" | "right" | "bottom";
  widthClass?: string;
  hideTitle?: boolean;
}

export function Dialog({ open, onOpenChange, title, description, children, footer, className, side, widthClass, hideTitle }: DialogProps) {
  const t = useT();
  const pos =
    side === "right"
      ? "right-0 top-0 h-full"
      : side === "left"
        ? "left-0 top-0 h-full"
        : side === "bottom"
          ? "bottom-0 left-0 w-full max-h-[90vh]"
          : "left-1/2 top-[10vh] -translate-x-1/2 max-h-[80vh] w-[min(640px,calc(100vw-32px))]";
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-[50] bg-slate-950/40" />
        <RD.Content
          className={cn(
            "fixed z-[50] flex flex-col overflow-hidden border border-line bg-surface text-body shadow-xl",
            side ? "" : "rounded-card",
            pos,
            side && side !== "bottom" ? (widthClass ?? "w-[320px]") : "",
            className,
          )}
          aria-describedby={description ? undefined : undefined}
        >
          <div className={cn("flex items-start justify-between gap-4 border-b border-line px-5 py-3", hideTitle && "nx-sr-only")}>
            <div>
              <RD.Title className="text-base font-bold text-fg">{title}</RD.Title>
              {description ? <RD.Description className="text-xs text-muted">{description}</RD.Description> : null}
            </div>
            <RD.Close aria-label={t("ds.close")} className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-control text-secondary hover:bg-nav-hover">
              <X aria-hidden="true" size={16} />
            </RD.Close>
          </div>
          {!description ? <RD.Description className="nx-sr-only">{title}</RD.Description> : null}
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer ? <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div> : null}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

export function Drawer(props: Omit<DialogProps, "side"> & { side?: "left" | "right" | "bottom" }) {
  return <Dialog side={props.side ?? "right"} {...props} />;
}
