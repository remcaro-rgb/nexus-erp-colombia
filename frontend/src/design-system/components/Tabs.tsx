// Tabs (owner P08) — WAI-ARIA tablist: arrows move focus+selection, Home/End jump, roving tabindex.
// Route-driven usage: pass `selected` and `onSelect` (deep-linkable tabs, UI-CONTRACT §19).
import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@lib/cn";

export interface TabDef {
  id: string;
  label: ReactNode;
}

export function Tabs({
  tabs,
  selected,
  onSelect,
  label,
  idPrefix,
  children,
}: {
  tabs: TabDef[];
  selected: string;
  onSelect: (id: string) => void;
  label: string;
  idPrefix: string;
  children?: ReactNode;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const idx = Math.max(0, tabs.findIndex((t) => t.id === selected));
  function move(next: number) {
    const n = (next + tabs.length) % tabs.length;
    const tab = tabs[n];
    if (!tab) return;
    onSelect(tab.id);
    refs.current[n]?.focus();
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowRight") move(idx + 1);
    else if (e.key === "ArrowLeft") move(idx - 1);
    else if (e.key === "Home") move(0);
    else if (e.key === "End") move(tabs.length - 1);
    else return;
    e.preventDefault();
  }
  return (
    <div>
      <div role="tablist" aria-label={label} className="flex gap-1 border-b border-line" onKeyDown={onKey}>
        {tabs.map((tab, i) => {
          const active = i === idx;
          return (
            <button
              key={tab.id}
              ref={(el) => {
                refs.current[i] = el;
              }}
              id={`${idPrefix}-tab-${tab.id}`}
              role="tab"
              type="button"
              aria-selected={active}
              aria-controls={`${idPrefix}-panel-${tab.id}`}
              tabIndex={active ? 0 : -1}
              onClick={() => onSelect(tab.id)}
              className={cn(
                "-mb-px min-h-8 border-b-2 px-3 py-2 text-sm font-medium",
                active ? "border-brand text-brand" : "border-transparent text-secondary hover:text-fg",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      {children !== undefined ? (
        <div role="tabpanel" id={`${idPrefix}-panel-${tabs[idx]?.id ?? ""}`} aria-labelledby={`${idPrefix}-tab-${tabs[idx]?.id ?? ""}`} tabIndex={0} className="pt-4">
          {children}
        </div>
      ) : null}
    </div>
  );
}
