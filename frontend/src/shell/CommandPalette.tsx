// Command palette (owner P08): Ctrl/Cmd+K (and "/") opens a modal command dialog with navigation, actions, recent
// records and policy-filtered server results (GET /api/v1/search), plus "Ask AI" which opens the AI panel with page
// context (route + record ids only). Combobox pattern with aria-activedescendant.
import * as RD from "@radix-ui/react-dialog";
import { Search, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useT, type MessageKey } from "@i18n/index";
import { cn } from "@lib/cn";
import type { SearchResponse } from "@lib/api/types";
import { prefKey, readPref, writePref } from "@lib/storage";
import { queueAiPrompt } from "./slots";
import { useShell } from "./ShellContext";

interface Item {
  id: string;
  group: string;
  label: string;
  hint?: string;
  run: () => void;
}

export function useCommandShortcut(open: () => void) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
      if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        open();
      } else if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        open();
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [open]);
}

export function rememberRecent(userId: string, entry: { label: string; route: string }) {
  const key = prefKey(userId, "palette.recent");
  const cur = readPref<Array<{ label: string; route: string }>>(key, []).filter((r) => r.route !== entry.route);
  writePref(key, [entry, ...cur].slice(0, 8));
}

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const t = useT();
  const shell = useShell();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [server, setServer] = useState<{ q: string; data: SearchResponse | null; error: boolean; loading: boolean }>({ q: "", data: null, error: false, loading: false });
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (!open) {
      setQ("");
      setActive(0);
    }
  }, [open]);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setServer({ q: term, data: null, error: false, loading: false });
      return;
    }
    const ctrl = new AbortController();
    setServer({ q: term, data: null, error: false, loading: true });
    const h = window.setTimeout(() => {
      shell.api
        .get<SearchResponse>("/api/v1/search", { params: { q: term, company_id: shell.companyId, limit: 20 }, signal: ctrl.signal })
        .then((data) => !ctrl.signal.aborted && setServer({ q: term, data, error: false, loading: false }))
        .catch(() => !ctrl.signal.aborted && setServer({ q: term, data: null, error: true, loading: false }));
    }, 200);
    return () => {
      window.clearTimeout(h);
      ctrl.abort();
    };
  }, [q, shell.api, shell.companyId]);

  const go = (label: string, route: string) => {
    rememberRecent(shell.userId, { label, route });
    onOpenChange(false);
    nav(route);
  };

  const items = useMemo<Item[]>(() => {
    const term = q.trim().toLocaleLowerCase("es-CO");
    const hit = (s: string) => !term || s.toLocaleLowerCase("es-CO").includes(term);
    const out: Item[] = [];
    for (const m of shell.registry.modules) {
      if (hit(m.label)) out.push({ id: `nav:${m.id}`, group: t("palette.navigation"), label: m.label, run: () => go(m.label, m.route) });
      for (const s of m.submenus) {
        if (s.route !== m.route && term && hit(`${m.label} ${s.label}`)) out.push({ id: `nav:${m.id}:${s.id}`, group: t("palette.navigation"), label: `${m.label} › ${s.label}`, run: () => go(`${m.label} › ${s.label}`, s.route) });
      }
    }
    for (const a of shell.registry.actions) {
      if (hit(a.label)) out.push({ id: `act:${a.id}`, group: t("palette.actions"), label: a.label, run: () => go(a.label, a.route) });
    }
    if (!term) {
      for (const r of readPref<Array<{ label: string; route: string }>>(prefKey(shell.userId, "palette.recent"), [])) {
        out.push({ id: `recent:${r.route}`, group: t("palette.recent"), label: r.label, run: () => go(r.label, r.route) });
      }
    }
    if (server.data && server.q === q.trim()) {
      for (const r of server.data.results) {
        const typeKey = `palette.type.${r.type}` as MessageKey;
        const group = typeKey in { "palette.type.documentos": 1, "palette.type.terceros": 1, "palette.type.productos": 1, "palette.type.cuentas": 1, "palette.type.empleados": 1 } ? t(typeKey) : t("palette.results");
        out.push({ id: `res:${r.type}:${r.id}`, group, label: r.title, hint: r.subtitle ?? undefined, run: () => go(r.title, r.href) });
      }
    }
    if (q.trim()) {
      out.push({
        id: "ai",
        group: t("header.ai"),
        label: t("palette.askAi", { q: q.trim() }),
        run: () => {
          queueAiPrompt(q.trim());
          onOpenChange(false);
          shell.setAiOpen(true);
        },
      });
    }
    return out;
  }, [q, server, shell, t]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const groups = [...new Set(items.map((i) => i.group))];
  const activeItem = items[active];
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-[50] bg-slate-950/40" />
        <RD.Content
          aria-describedby={undefined}
          data-nx-region="command-palette"
          className="fixed left-1/2 top-[12vh] z-[50] w-[min(640px,calc(100vw-32px))] -translate-x-1/2 overflow-hidden rounded-card border border-line bg-surface shadow-2xl"
        >
          <RD.Title className="nx-sr-only">{t("palette.title")}</RD.Title>
          <div className="flex items-center gap-2 border-b border-line px-4">
            <Search aria-hidden="true" size={16} className="text-muted" />
            <input
              autoFocus
              role="combobox"
              aria-expanded="true"
              aria-controls="nx-palette-list"
              aria-autocomplete="list"
              aria-activedescendant={activeItem ? `nx-pal-${active}` : undefined}
              aria-label={t("palette.placeholder")}
              placeholder={t("palette.placeholder")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") setActive((a) => Math.min(items.length - 1, a + 1));
                else if (e.key === "ArrowUp") setActive((a) => Math.max(0, a - 1));
                else if (e.key === "Home") setActive(0);
                else if (e.key === "End") setActive(items.length - 1);
                else if (e.key === "Enter") activeItem?.run();
                else return;
                e.preventDefault();
              }}
              className="h-12 flex-1 bg-transparent text-sm text-body outline-none placeholder:text-muted"
            />
          </div>
          <ul id="nx-palette-list" ref={listRef} role="listbox" aria-label={t("palette.title")} className="max-h-[50vh] overflow-y-auto p-2">
            {groups.map((g) => (
              <li key={g} role="presentation">
                <p aria-hidden="true" className="px-3 pb-1 pt-2 text-xs font-semibold uppercase tracking-wider text-muted">
                  {g}
                </p>
                <ul role="group" aria-label={g}>
                  {items.map((it, i) =>
                    it.group !== g ? null : (
                      <li
                        key={it.id}
                        id={`nx-pal-${i}`}
                        data-index={i}
                        role="option"
                        aria-selected={i === active}
                        onMouseEnter={() => setActive(i)}
                        onClick={() => it.run()}
                        className={cn("flex min-h-8 cursor-pointer items-center gap-2 rounded-control px-3 py-2 text-sm", i === active ? "bg-nav-active text-nav-active-fg" : "text-body")}
                      >
                        {it.id === "ai" ? <Sparkles aria-hidden="true" size={14} /> : null}
                        <span className="truncate">{it.label}</span>
                        {it.hint ? <span className="ml-auto truncate text-xs text-muted">{it.hint}</span> : null}
                      </li>
                    ),
                  )}
                </ul>
              </li>
            ))}
          </ul>
          <p role="status" aria-live="polite" className="border-t border-line px-4 py-2 text-xs text-muted">
            {server.loading ? t("palette.searching") : server.error ? t("palette.error") : q.trim() && items.length <= 1 ? t("palette.empty", { q: q.trim() }) : ""}
          </p>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
