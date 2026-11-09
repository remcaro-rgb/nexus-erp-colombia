// Typed calls to the ledger dimension API (contracts/openapi/modules/ledger.yaml, F-ACC-DIM). The UI uses only the public API.
import type { ApiClient } from "@lib/api/client";
import type { TreeNode } from "@ds/tree-table";
import type { DimensionTab } from "./config";

export interface DimensionNodeDto {
  id: string;
  company_id: string;
  code: string;
  description: string;
  node_type: "AGRUPADOR" | "DETALLE";
  parent_id: string | null;
  valid_from: string | null;
  valid_to: string | null;
  status: "ACTIVE" | "ARCHIVED";
  sort_key: string | null;
  path: string;
  depth: number;
  version: number;
  children_count?: number;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface UsageDto {
  references: number;
  by_type?: Record<string, number>;
}

export interface NodeInput {
  code: string;
  description: string;
  node_type: "AGRUPADOR" | "DETALLE";
  parent_id: string | null;
  valid_from?: string;
}

export function toTreeNode(n: DimensionNodeDto): TreeNode {
  return {
    id: n.id,
    parentId: n.parent_id,
    code: n.code,
    description: n.description,
    nodeType: n.node_type,
    status: n.status,
    sortKey: n.sort_key ?? undefined,
  };
}

export class DimensionApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  private base(dim: DimensionTab): string {
    return `/api/v1/ledger/dimensions/${dim.slug}`;
  }

  /** Whole dimension (all pages): one dataset feeds both the table and the tree view. */
  async list(dim: DimensionTab, signal?: AbortSignal): Promise<DimensionNodeDto[]> {
    const out: DimensionNodeDto[] = [];
    let cursor: string | null = null;
    do {
      const page: Page<DimensionNodeDto> = await this.api.get(this.base(dim), {
        params: { limit: 500, sort: "path", cursor },
        headers: this.headers(),
        signal,
      });
      out.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    return out;
  }

  create(dim: DimensionTab, body: NodeInput): Promise<DimensionNodeDto> {
    return this.api.post(this.base(dim), { body, headers: this.headers() });
  }

  move(dim: DimensionTab, id: string, parentId: string | null): Promise<DimensionNodeDto> {
    return this.api.post(`${this.base(dim)}/${id}:move`, { body: { parameters: { parent_id: parentId } }, headers: this.headers() });
  }

  archive(dim: DimensionTab, id: string, reason: string): Promise<DimensionNodeDto> {
    return this.api.post(`${this.base(dim)}/${id}:archive`, { body: { reason }, headers: this.headers() });
  }

  unarchive(dim: DimensionTab, id: string): Promise<DimensionNodeDto> {
    return this.api.post(`${this.base(dim)}/${id}:unarchive`, { body: {}, headers: this.headers() });
  }

  usage(dim: DimensionTab, id: string, signal?: AbortSignal): Promise<UsageDto> {
    return this.api.get(`${this.base(dim)}/${id}/usage`, { headers: this.headers(), signal });
  }
}
