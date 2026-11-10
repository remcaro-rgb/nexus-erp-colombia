// TreeTable data model (owner P08). One flat dataset feeds both the table view and the tree view.
export type NodeType = "AGRUPADOR" | "DETALLE";
export type NodeStatus = "ACTIVE" | "ARCHIVED";

export interface TreeNode {
  id: string;
  parentId: string | null;
  code: string;
  description: string;
  nodeType: NodeType;
  status: NodeStatus;
  /** Lazy loading: true when the node has children that are not in the dataset yet. */
  hasChildren?: boolean;
  sortKey?: string;
}

export type SortField = "code" | "description" | "path";
export interface SortState {
  field: SortField;
  dir: "asc" | "desc";
}

const collator = new Intl.Collator("es-CO", { numeric: true, sensitivity: "base" });

export interface TreeIndex {
  byId: Map<string, TreeNode>;
  children: Map<string | null, TreeNode[]>;
  path: (id: string) => TreeNode[];
}

export function buildIndex(nodes: readonly TreeNode[], sort: SortState): TreeIndex {
  const byId = new Map<string, TreeNode>();
  for (const n of nodes) byId.set(n.id, n);
  const children = new Map<string | null, TreeNode[]>();
  for (const n of nodes) {
    const parent = n.parentId !== null && byId.has(n.parentId) ? n.parentId : null;
    const list = children.get(parent);
    if (list) list.push(n);
    else children.set(parent, [n]);
  }
  const dir = sort.dir === "asc" ? 1 : -1;
  const key = (n: TreeNode) => (sort.field === "description" ? n.description : (n.sortKey ?? n.code));
  for (const list of children.values()) list.sort((a, b) => dir * collator.compare(key(a), key(b)));
  const pathCache = new Map<string, TreeNode[]>();
  const path = (id: string): TreeNode[] => {
    const hit = pathCache.get(id);
    if (hit) return hit;
    const out: TreeNode[] = [];
    const seen = new Set<string>();
    let cur = byId.get(id);
    while (cur && !seen.has(cur.id)) {
      seen.add(cur.id);
      out.unshift(cur);
      cur = cur.parentId ? byId.get(cur.parentId) : undefined;
    }
    pathCache.set(id, out);
    return out;
  };
  return { byId, children, path };
}

export function matches(n: TreeNode, q: string): boolean {
  if (!q) return true;
  const s = q.toLocaleLowerCase("es-CO");
  return n.code.toLocaleLowerCase("es-CO").includes(s) || n.description.toLocaleLowerCase("es-CO").includes(s);
}

export interface VisibleRow {
  node: TreeNode;
  level: number; // 1-based (aria-level)
  posInSet: number;
  setSize: number;
  expandable: boolean;
  expanded: boolean;
  match: boolean;
}

/** Depth-first visible rows of the tree view. With a query: matches + their ancestors (auto-expanded). */
export function visibleTreeRows(idx: TreeIndex, expanded: ReadonlySet<string>, query: string): VisibleRow[] {
  const keep = new Set<string>();
  if (query) {
    for (const n of idx.byId.values()) {
      if (matches(n, query)) for (const p of idx.path(n.id)) keep.add(p.id);
    }
  }
  const out: VisibleRow[] = [];
  const walk = (parent: string | null, level: number) => {
    const all = idx.children.get(parent) ?? [];
    const list = query ? all.filter((c) => keep.has(c.id)) : all;
    list.forEach((n, i) => {
      const kids = idx.children.get(n.id);
      const expandable = Boolean((kids && kids.length) || n.hasChildren);
      const isOpen = expandable && (query ? true : expanded.has(n.id));
      out.push({ node: n, level, posInSet: i + 1, setSize: list.length, expandable, expanded: isOpen, match: query ? matches(n, query) : false });
      if (isOpen) walk(n.id, level + 1);
    });
  };
  walk(null, 1);
  return out;
}

/** Flat rows of the table view (same dataset), filtered by the query and sorted. */
export function tableRows(idx: TreeIndex, query: string, sort: SortState): TreeNode[] {
  const rows = [...idx.byId.values()].filter((n) => matches(n, query));
  const dir = sort.dir === "asc" ? 1 : -1;
  const pathStr = (n: TreeNode) => idx.path(n.id).map((p) => p.code).join(" / ");
  const key =
    sort.field === "description" ? (n: TreeNode) => n.description : sort.field === "path" ? pathStr : (n: TreeNode) => n.sortKey ?? n.code;
  return rows.sort((a, b) => dir * collator.compare(key(a), key(b)));
}
