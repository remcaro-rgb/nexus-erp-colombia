// Stateful contract fake of /api/v1/ledger/dimensions/* (contracts/openapi/modules/ledger.yaml) for the browser e2e
// T-ACC-DIM-08. It answers like the real API for the UI flows (shapes, status codes, problem+json codes, Idempotency-Key
// and X-Company-Id handling). The hierarchy rules themselves are proven against PostgreSQL by T-ACC-DIM-01…07
// (backend/ledger/tests). Synthetic data only.
export interface FakeRequest {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

export interface FakeResponse {
  status: number;
  body: unknown;
  contentType: string;
}

interface Node {
  id: string;
  company_id: string;
  dimension: string;
  code: string;
  description: string;
  node_type: "AGRUPADOR" | "DETALLE";
  parent_id: string | null;
  valid_from: string;
  valid_to: string | null;
  status: "ACTIVE" | "ARCHIVED";
  sort_key: string | null;
  external_ref: string | null;
  path: string;
  depth: number;
  version: number;
}

const SLUGS: Record<string, string> = {
  "cost-centers": "cost_center",
  "business-units": "business_unit",
  "aux-abierto": "aux_abierto",
  "item-contable": "item_contable",
};
const MAX_DEPTH = 6;

export class DimensionContractFake {
  readonly nodes = new Map<string, Node>();
  readonly log: FakeRequest[] = [];
  readonly usage = new Map<string, number>();
  private seq = 0;

  constructor(readonly companyId: string) {}

  private problem(status: number, code: string, detail: string): FakeResponse {
    return { status, contentType: "application/problem+json", body: { type: `https://errors.nexus-erp.invalid/${code}`, title: code, status, code, detail, trace_id: `fake-${this.seq}` } };
  }

  private ok(status: number, body: unknown): FakeResponse {
    return { status, body, contentType: "application/json" };
  }

  private newId(): string {
    this.seq += 1;
    return `0190f000-0000-7000-8000-${this.seq.toString(16).padStart(12, "0")}`;
  }

  private children(id: string): Node[] {
    return [...this.nodes.values()].filter((n) => n.parent_id === id);
  }

  private height(id: string): number {
    const kids = this.children(id);
    return kids.length ? 1 + Math.max(...kids.map((k) => this.height(k.id))) : 0;
  }

  private descendants(id: string): Set<string> {
    const out = new Set<string>();
    const walk = (p: string) => {
      for (const c of this.children(p)) {
        out.add(c.id);
        walk(c.id);
      }
    };
    walk(id);
    return out;
  }

  private refresh(n: Node): void {
    const parent = n.parent_id ? this.nodes.get(n.parent_id) : undefined;
    n.depth = parent ? parent.depth + 1 : 1;
    n.path = parent ? `${parent.path}/${n.code}` : n.code;
    for (const c of this.children(n.id)) this.refresh(c);
  }

  private parentCheck(dimension: string, parentId: unknown): Node | FakeResponse | null {
    if (parentId === null || parentId === undefined || parentId === "") return null;
    const p = this.nodes.get(String(parentId));
    if (!p || p.company_id !== this.companyId) return this.problem(422, "dimension_cross_company", "the parent is not a node of this company and dimension");
    if (p.dimension !== dimension) return this.problem(422, "validation_failed", "the parent belongs to another dimension");
    if (p.node_type !== "AGRUPADOR") return this.problem(422, "validation_failed", "the parent must be an AGRUPADOR");
    if (p.status !== "ACTIVE") return this.problem(422, "dimension_archived", "the parent is archived");
    return p;
  }

  handle(req: FakeRequest): FakeResponse {
    this.log.push(req);
    const url = new URL(req.url);
    const m = url.pathname.match(/^\/api\/v1\/ledger\/dimensions\/([a-z-]+?)(?:\/([0-9a-f-]{36}))?(?::(archive|unarchive|move)|\/(usage|tree))?$/);
    if (!m) return this.problem(404, "not_found", "unknown route");
    const [, slug, id, action, sub] = m;
    const dimension = SLUGS[slug ?? ""];
    if (!dimension) return this.problem(404, "not_found", "unknown dimension");
    const company = req.headers["x-company-id"];
    if (company !== this.companyId) return this.problem(422, "validation_failed", "company not available in the current context");
    if (req.method !== "GET" && !req.headers["idempotency-key"]) return this.problem(400, "idempotency_key_required", "Idempotency-Key header required");
    const body = (req.body ?? {}) as Record<string, unknown>;

    if (!id && req.method === "GET") {
      const items = [...this.nodes.values()].filter((n) => n.dimension === dimension).sort((a, b) => a.path.localeCompare(b.path));
      return this.ok(200, { items: items.map((n) => ({ ...n, children_count: this.children(n.id).length })), next_cursor: null });
    }
    if (!id && req.method === "POST") {
      const code = String(body["code"] ?? "").trim();
      const description = String(body["description"] ?? "").trim();
      const nodeType = body["node_type"];
      if (!code || code.length > 40 || !description || (nodeType !== "AGRUPADOR" && nodeType !== "DETALLE")) return this.problem(422, "validation_failed", "invalid node");
      if ([...this.nodes.values()].some((n) => n.dimension === dimension && n.code.toLowerCase() === code.toLowerCase()))
        return this.problem(422, "dimension_code_duplicate", "code already exists");
      const parent = this.parentCheck(dimension, body["parent_id"]);
      if (parent && "status" in parent && typeof parent.status === "number") return parent as FakeResponse;
      const p = parent as Node | null;
      if ((p ? p.depth + 1 : 1) > MAX_DEPTH) return this.problem(422, "dimension_depth_exceeded", "depth exceeded");
      const n: Node = {
        id: this.newId(), company_id: this.companyId, dimension, code, description, node_type: nodeType,
        parent_id: p ? p.id : null, valid_from: "2026-11-15", valid_to: null, status: "ACTIVE", sort_key: null,
        external_ref: null, path: "", depth: 1, version: 1,
      };
      this.nodes.set(n.id, n);
      this.refresh(n);
      return this.ok(201, n);
    }
    const node = id ? this.nodes.get(id) : undefined;
    if (!node || node.dimension !== dimension) return this.problem(404, "not_found", "dimension node not found");
    if (sub === "usage") return this.ok(200, { references: this.usage.get(node.id) ?? 0, by_type: { journal_lines: this.usage.get(node.id) ?? 0, children: this.children(node.id).length } });
    if (action === "archive") {
      node.status = "ARCHIVED";
      node.version += 1;
      return this.ok(200, node);
    }
    if (action === "unarchive") {
      node.status = "ACTIVE";
      node.version += 1;
      return this.ok(200, node);
    }
    if (action === "move") {
      const params = (body["parameters"] ?? {}) as Record<string, unknown>;
      const target = params["parent_id"] ?? null;
      if (target === node.id || (target && this.descendants(node.id).has(String(target)))) return this.problem(422, "dimension_cycle", "cycle");
      const parent = this.parentCheck(dimension, target);
      if (parent && "status" in parent && typeof parent.status === "number") return parent as FakeResponse;
      const p = parent as Node | null;
      if ((p ? p.depth + 1 : 1) + this.height(node.id) > MAX_DEPTH) return this.problem(422, "dimension_depth_exceeded", "depth exceeded");
      node.parent_id = p ? p.id : null;
      node.version += 1;
      this.refresh(node);
      return this.ok(200, node);
    }
    if (req.method === "GET") return this.ok(200, node);
    return this.problem(405, "method_not_allowed", "not supported by the fake");
  }
}
