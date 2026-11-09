// TreeTable (owner P08; consumer P05 Contabilidad › Dimensiones, UI-CONTRACT §18, F-ACC-DIM).
// * Table view and tree view of the SAME dataset (toggle in the header or controlled by the page).
// * Tree view = WAI-ARIA tree: role=tree + treeitem (aria-level/posinset/setsize/expanded/selected), focus via
//   aria-activedescendant; keys ↑ ↓ → ← Home End Enter Space * and type-ahead.
// * Lazy children (loadChildren), search with highlighted match and ancestor path, sort, inline edit hook,
//   node-type badge Agrupador/Detalle, archived rows muted WITH a text label, virtualized rows (10,000+ nodes).
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Pencil } from "lucide-react";
import { cn } from "@lib/cn";
import { useT } from "@i18n/index";
import { Badge } from "../components/Badge";
import { useFixedVirtual } from "../virtual/useFixedVirtual";
import { buildIndex, matches, tableRows, visibleTreeRows, type SortField, type SortState, type TreeNode, type VisibleRow } from "./model";

export type TreeTableView = "table" | "tree";

export interface TreeTableProps {
  /** Accessible name of the tree/table (e.g. "Centros de costo"). */
  label: string;
  nodes: readonly TreeNode[];
  view?: TreeTableView;
  defaultView?: TreeTableView;
  onViewChange?: (v: TreeTableView) => void;
  /** Hide the built-in view toggle when the page header owns it. */
  hideViewToggle?: boolean;
  /** Lazy children for nodes with hasChildren=true. */
  loadChildren?: (parentId: string) => Promise<TreeNode[]>;
  onActivate?: (node: TreeNode) => void;
  onSelect?: (node: TreeNode | null) => void;
  /** Inline edit hook: renders an edit button per row. */
  onEdit?: (node: TreeNode) => void;
  renderActions?: (node: TreeNode) => ReactNode;
  defaultExpanded?: readonly string[];
  rowHeight?: number;
  height?: number;
  initialQuery?: string;
}

function Highlight({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  const i = text.toLocaleLowerCase("es-CO").indexOf(q.toLocaleLowerCase("es-CO"));
  if (i < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <mark className="rounded bg-amber-50 px-0.5 text-amber-700 dark:bg-amber-700/30 dark:text-amber-500">{text.slice(i, i + q.length)}</mark>
      {text.slice(i + q.length)}
    </>
  );
}

export function TreeTable(props: TreeTableProps) {
  const {
    label,
    nodes,
    loadChildren,
    onActivate,
    onSelect,
    onEdit,
    renderActions,
    hideViewToggle,
    rowHeight = 36,
    height = 480,
    initialQuery = "",
  } = props;
  const t = useT();
  const uid = useId().replace(/:/g, "");
  const [viewState, setViewState] = useState<TreeTableView>(props.defaultView ?? "tree");
  const view = props.view ?? viewState;
  const setView = (v: TreeTableView) => {
    setViewState(v);
    props.onViewChange?.(v);
  };
  const [query, setQuery] = useState(initialQuery);
  const [sort, setSort] = useState<SortState>({ field: "code", dir: "asc" });
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(props.defaultExpanded ?? []));
  const [lazy, setLazy] = useState<TreeNode[]>([]);
  const [loading, setLoading] = useState<Set<string>>(() => new Set());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const typeahead = useRef<{ s: string; at: number }>({ s: "", at: 0 });

  const all = useMemo(() => {
    if (!lazy.length) return nodes;
    const have = new Set(nodes.map((n) => n.id));
    return [...nodes, ...lazy.filter((l) => !have.has(l.id))];
  }, [nodes, lazy]);
  const idx = useMemo(() => buildIndex(all, sort.field === "path" ? { field: "code", dir: sort.dir } : sort), [all, sort]);
  const q = query.trim();
  const treeRows = useMemo(() => (view === "tree" ? visibleTreeRows(idx, expanded, q) : []), [view, idx, expanded, q]);
  const flatRows = useMemo(() => (view === "table" ? tableRows(idx, q, sort) : []), [view, idx, q, sort]);
  const count = view === "tree" ? treeRows.length : flatRows.length;
  const matchCount = useMemo(() => (q ? [...idx.byId.values()].filter((n) => matches(n, q)).length : idx.byId.size), [idx, q]);
  const virt = useFixedVirtual(count, rowHeight, height);

  const activeIndex = useMemo(() => {
    if (view !== "tree") return -1;
    const i = treeRows.findIndex((r) => r.node.id === activeId);
    return i;
  }, [treeRows, activeId, view]);

  useEffect(() => {
    if (view === "tree" && activeIndex < 0 && treeRows.length) setActiveId(treeRows[0]?.node.id ?? null);
  }, [view, activeIndex, treeRows]);

  const ensureChildren = useCallback(
    async (node: TreeNode) => {
      if (!loadChildren || !node.hasChildren || idx.children.get(node.id)?.length) return;
      setLoading((s) => new Set(s).add(node.id));
      try {
        const kids = await loadChildren(node.id);
        setLazy((cur) => [...cur, ...kids]);
      } finally {
        setLoading((s) => {
          const n = new Set(s);
          n.delete(node.id);
          return n;
        });
      }
    },
    [loadChildren, idx],
  );

  const toggle = useCallback(
    (row: VisibleRow, open?: boolean) => {
      if (!row.expandable) return;
      const next = open ?? !row.expanded;
      setExpanded((s) => {
        const n = new Set(s);
        if (next) n.add(row.node.id);
        else n.delete(row.node.id);
        return n;
      });
      if (next) void ensureChildren(row.node);
    },
    [ensureChildren],
  );

  const focusRow = (i: number) => {
    const r = treeRows[Math.max(0, Math.min(treeRows.length - 1, i))];
    if (!r) return;
    setActiveId(r.node.id);
    virt.scrollToIndex(treeRows.indexOf(r));
  };

  const select = (node: TreeNode) => {
    setSelectedId(node.id);
    onSelect?.(node);
  };

  function onTreeKey(e: KeyboardEvent<HTMLDivElement>) {
    const i = activeIndex < 0 ? 0 : activeIndex;
    const row = treeRows[i];
    if (!row) return;
    switch (e.key) {
      case "ArrowDown":
        focusRow(i + 1);
        break;
      case "ArrowUp":
        focusRow(i - 1);
        break;
      case "Home":
        focusRow(0);
        break;
      case "End":
        focusRow(treeRows.length - 1);
        break;
      case "ArrowRight":
        if (row.expandable && !row.expanded) toggle(row, true);
        else if (row.expanded) focusRow(i + 1);
        break;
      case "ArrowLeft":
        if (row.expanded) toggle(row, false);
        else if (row.node.parentId) {
          const p = treeRows.findIndex((r) => r.node.id === row.node.parentId);
          if (p >= 0) focusRow(p);
        }
        break;
      case "Enter":
        select(row.node);
        onActivate?.(row.node);
        break;
      case " ":
        select(row.node);
        break;
      case "*": {
        const siblings = idx.children.get(row.node.parentId && idx.byId.has(row.node.parentId) ? row.node.parentId : null) ?? [];
        setExpanded((s) => {
          const n = new Set(s);
          for (const sib of siblings) if (idx.children.get(sib.id)?.length || sib.hasChildren) n.add(sib.id);
          return n;
        });
        for (const sib of siblings) void ensureChildren(sib);
        break;
      }
      default:
        if (e.key.length === 1 && /\S/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
          const now = Date.now();
          const ta = typeahead.current;
          ta.s = now - ta.at < 600 ? ta.s + e.key.toLowerCase() : e.key.toLowerCase();
          ta.at = now;
          const order = [...treeRows.slice(i + 1), ...treeRows.slice(0, i + 1)];
          const hit = order.find((r) => r.node.code.toLowerCase().startsWith(ta.s) || r.node.description.toLowerCase().startsWith(ta.s));
          if (hit) focusRow(treeRows.indexOf(hit));
          break;
        }
        return;
    }
    e.preventDefault();
  }

  const sortBy = (field: SortField) =>
    setSort((s) => (s.field === field ? { field, dir: s.dir === "asc" ? "desc" : "asc" } : { field, dir: "asc" }));
  const ariaSort = (field: SortField) => (sort.field === field ? (sort.dir === "asc" ? "ascending" : "descending") : "none");

  const typeBadge = (n: TreeNode) =>
    n.nodeType === "AGRUPADOR" ? <Badge tone="brand">{t("tree.agrupador")}</Badge> : <Badge tone="neutral">{t("tree.detalle")}</Badge>;
  const archivedBadge = (n: TreeNode) => (n.status === "ARCHIVED" ? <Badge tone="warning">{t("tree.archived")}</Badge> : null);
  const actions = (n: TreeNode) => (
    <span className="ml-auto flex items-center gap-1">
      {renderActions?.(n)}
      {onEdit ? (
        <button
          type="button"
          tabIndex={view === "tree" ? -1 : 0}
          aria-label={t("tree.edit", { code: n.code })}
          onClick={(e) => {
            e.stopPropagation();
            onEdit(n);
          }}
          className="inline-flex min-h-6 min-w-6 items-center justify-center rounded-control text-secondary hover:bg-nav-hover"
        >
          <Pencil aria-hidden="true" size={14} />
        </button>
      ) : null}
    </span>
  );

  const pathText = (n: TreeNode) =>
    idx
      .path(n.id)
      .slice(0, -1)
      .map((p) => p.code)
      .join(" / ");

  return (
    <div className="flex flex-col gap-3" data-nx-component="tree-table" data-view={view}>
      <div className="flex flex-wrap items-center gap-2">
        <label className="nx-sr-only" htmlFor={`${uid}-q`}>
          {t("tree.search")}
        </label>
        <input
          id={`${uid}-q`}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("tree.search")}
          className="min-h-8 w-72 rounded-control border border-line bg-input px-3 py-1.5 text-sm text-body placeholder:text-muted"
        />
        <p role="status" aria-live="polite" data-nx-tree-status="" className="text-xs text-muted">
          {t("tree.matches", { count: matchCount })}
        </p>
        {!hideViewToggle ? (
          <div role="group" aria-label={t("tree.view")} className="ml-auto inline-flex rounded-control border border-line p-0.5">
            {(["table", "tree"] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className={cn("min-h-8 rounded-control px-3 text-xs font-semibold", view === v ? "bg-nav-active text-nav-active-fg" : "text-secondary hover:bg-nav-hover")}
              >
                {v === "table" ? t("tree.viewTable") : t("tree.viewTree")}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {view === "tree" ? (
        <div
          ref={virt.ref}
          onScroll={virt.onScroll}
          role="tree"
          aria-label={label}
          tabIndex={0}
          aria-activedescendant={activeId ? `${uid}-n-${activeId}` : undefined}
          onKeyDown={onTreeKey}
          className="relative overflow-y-auto rounded-card border border-line bg-surface"
          style={{ height }}
          data-total-rows={count}
        >
          <div style={{ height: virt.totalHeight, position: "relative" }} role="none">
            <div style={{ transform: `translateY(${virt.offsetTop}px)` }} role="none">
              {treeRows.slice(virt.start, virt.end).map((r) => {
                const n = r.node;
                const active = n.id === activeId;
                const archived = n.status === "ARCHIVED";
                return (
                  <div
                    key={n.id}
                    id={`${uid}-n-${n.id}`}
                    role="treeitem"
                    aria-level={r.level}
                    aria-posinset={r.posInSet}
                    aria-setsize={r.setSize}
                    aria-expanded={r.expandable ? r.expanded : undefined}
                    aria-selected={selectedId === n.id}
                    aria-busy={loading.has(n.id) || undefined}
                    data-node-id={n.id}
                    onClick={() => {
                      setActiveId(n.id);
                      select(n);
                      if (r.expandable) toggle(r);
                    }}
                    className={cn(
                      "flex cursor-pointer items-center gap-2 border-b border-line-subtle pr-3 text-sm",
                      active && "outline-2 -outline-offset-2 outline-ring",
                      selectedId === n.id && "bg-nav-active",
                      archived ? "text-muted" : "text-body",
                    )}
                    style={{ height: rowHeight, paddingLeft: 8 + (r.level - 1) * 20 }}
                  >
                    <span aria-hidden="true" className="inline-flex w-4 justify-center">
                      {r.expandable ? r.expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : null}
                    </span>
                    <span className={cn("font-mono text-xs font-semibold", archived && "line-through")}>
                      <Highlight text={n.code} q={q} />
                    </span>
                    <span className="truncate">
                      <Highlight text={n.description} q={q} />
                    </span>
                    {typeBadge(n)}
                    {archivedBadge(n)}
                    {q && r.level > 1 && r.match ? <span className="truncate text-xs text-muted">{pathText(n)}</span> : null}
                    {loading.has(n.id) ? <span className="text-xs text-muted">{t("tree.loading")}</span> : null}
                    {actions(n)}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      ) : (
        <div role="table" aria-label={label} aria-rowcount={count + 1} className="flex flex-col rounded-card border border-line bg-surface">
          <div role="rowgroup">
            <div role="row" aria-rowindex={1} className="grid grid-cols-[140px_1fr_110px_1fr_100px_60px] border-b border-line text-xs font-semibold text-muted">
              {(
                [
                  ["code", t("tree.code")],
                  ["description", t("tree.description")],
                ] as const
              ).map(([f, l]) => (
                <div key={f} role="columnheader" aria-sort={ariaSort(f)} className="px-3 py-2">
                  <button type="button" onClick={() => sortBy(f)} className="min-h-6 font-semibold hover:text-fg">
                    {l}
                  </button>
                </div>
              ))}
              <div role="columnheader" className="px-3 py-2">
                {t("tree.type")}
              </div>
              <div role="columnheader" aria-sort={ariaSort("path")} className="px-3 py-2">
                <button type="button" onClick={() => sortBy("path")} className="min-h-6 font-semibold hover:text-fg">
                  {t("tree.path")}
                </button>
              </div>
              <div role="columnheader" className="px-3 py-2">
                {t("tree.status")}
              </div>
              <div role="columnheader" className="px-3 py-2">
                <span className="nx-sr-only">{t("doc.actions")}</span>
              </div>
            </div>
          </div>
          <div ref={virt.ref} onScroll={virt.onScroll} role="rowgroup" className="relative overflow-y-auto" style={{ height }} data-total-rows={count}>
            <div style={{ height: virt.totalHeight, position: "relative" }} role="none">
              <div style={{ transform: `translateY(${virt.offsetTop}px)` }} role="none">
                {flatRows.slice(virt.start, virt.end).map((n, k) => {
                  const archived = n.status === "ARCHIVED";
                  return (
                    <div
                      key={n.id}
                      role="row"
                      aria-rowindex={virt.start + k + 2}
                      aria-selected={selectedId === n.id}
                      data-node-id={n.id}
                      onClick={() => select(n)}
                      className={cn(
                        "grid grid-cols-[140px_1fr_110px_1fr_100px_60px] items-center border-b border-line-subtle text-sm",
                        archived ? "text-muted" : "text-body",
                        selectedId === n.id && "bg-nav-active",
                      )}
                      style={{ height: rowHeight }}
                    >
                      <div role="cell" className={cn("truncate px-3 font-mono text-xs font-semibold", archived && "line-through")}>
                        <Highlight text={n.code} q={q} />
                      </div>
                      <div role="cell" className="truncate px-3">
                        <Highlight text={n.description} q={q} />
                      </div>
                      <div role="cell" className="px-3">
                        {typeBadge(n)}
                      </div>
                      <div role="cell" className="truncate px-3 text-xs text-muted">
                        {pathText(n)}
                      </div>
                      <div role="cell" className="px-3">
                        {archived ? archivedBadge(n) : <span className="text-xs">{t("tree.active")}</span>}
                      </div>
                      <div role="cell" className="flex px-3">
                        {actions(n)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
