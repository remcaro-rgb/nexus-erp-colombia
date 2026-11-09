// Toasts (owner P08). Success toasts are shown only after the API confirmed (UI-CONTRACT §12); error toasts carry
// the correlation/trace id. Announced through a polite live region (role="status") or assertive for errors.
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@lib/cn";
import { useT } from "@i18n/index";

export type ToastTone = "info" | "success" | "error";

export interface ToastInput {
  tone: ToastTone;
  title: string;
  body?: string;
  /** Correlation id (X-Request-Id / problem.trace_id). */
  traceId?: string | null;
}

interface ToastItem extends ToastInput {
  id: number;
}

interface ToastApi {
  push: (t: ToastInput) => void;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const t = useT();
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (input: ToastInput) => {
      const id = ++seq.current;
      setItems((xs) => [...xs.slice(-4), { ...input, id }]);
      if (input.tone !== "error") window.setTimeout(() => dismiss(id), 6000);
    },
    [dismiss],
  );
  const api = useMemo(() => ({ push, dismiss }), [push, dismiss]);
  const polite = items.filter((i) => i.tone !== "error");
  const assertive = items.filter((i) => i.tone === "error");
  const render = (it: ToastItem) => (
    <li
      key={it.id}
      className={cn(
        "pointer-events-auto flex w-80 items-start gap-3 rounded-card border bg-surface p-3 shadow-lg",
        it.tone === "error" ? "border-rose-600" : it.tone === "success" ? "border-emerald-600" : "border-line",
      )}
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-fg">{it.title}</p>
        {it.body ? <p className="text-xs text-body">{it.body}</p> : null}
        {it.traceId ? <p className="mt-1 font-mono text-xs text-muted">{t("ds.toast.trace", { trace: it.traceId })}</p> : null}
      </div>
      <button
        type="button"
        aria-label={t("ds.toast.dismiss")}
        onClick={() => dismiss(it.id)}
        className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-control text-secondary hover:bg-nav-hover"
      >
        <X aria-hidden="true" size={14} />
      </button>
    </li>
  );
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex flex-col gap-2">
        <ul role="status" aria-live="polite" className="flex flex-col gap-2">
          {polite.map(render)}
        </ul>
        <ul role="alert" aria-live="assertive" className="flex flex-col gap-2">
          {assertive.map(render)}
        </ul>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const v = useContext(ToastContext);
  if (!v) throw new Error("useToast must be used inside <ToastProvider>");
  return v;
}
