/**
 * Typed client for the IAM endpoints used by Administración (branches, portal accounts, /me).
 * The UI uses only the public API: every mutation sends an Idempotency-Key, PATCH sends If-Match,
 * the active company travels in X-Company-Id, and errors arrive as RFC 9457 problem+json.
 */

export interface Problem {
  readonly type: string;
  readonly title: string;
  readonly status: number;
  readonly code: string;
  readonly detail?: string;
  readonly errors?: unknown;
}

export interface Branch {
  readonly id: string;
  readonly company_id: string;
  readonly code: string;
  readonly name: string;
  readonly status: "ACTIVE" | "ARCHIVED";
  readonly is_head_office: boolean;
  readonly address?: string;
  readonly municipality_code?: string | null;
  readonly phone?: string | null;
  readonly default_business_unit_id?: string | null;
  readonly default_warehouse_id?: string | null;
  readonly valid_from?: string;
  readonly valid_to?: string | null;
  readonly version: number;
}

export type BranchInput = Partial<Omit<Branch, "id" | "company_id" | "status" | "version">> & { code?: string; name?: string };

export interface ExternalAccount {
  readonly id: string;
  readonly email?: string;
  readonly portal_kind: "supplier" | "customer" | "employee";
  readonly party: { readonly id: string; readonly kind?: string };
  readonly external_status: "INVITED" | "ACTIVE" | "SUSPENDED" | "REVOKED" | "EXPIRED";
  readonly invitation_expires_at?: string | null;
  readonly last_login_at?: string | null;
  readonly version: number;
}

export interface Page<T> {
  readonly items: readonly T[];
  readonly next_cursor: string | null;
}

export class ApiError extends Error {
  constructor(readonly problem: Problem) {
    super(problem.title);
  }
}

export interface IamClientOptions {
  readonly baseUrl?: string;
  readonly token: () => string | null;
  readonly companyId: () => string | null;
}

export function createIamClient(opts: IamClientOptions) {
  const base = opts.baseUrl ?? "";

  async function call<T>(method: string, path: string, body?: unknown, extra: Record<string, string> = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json", ...extra };
    const token = opts.token();
    if (token) headers.Authorization = `Bearer ${token}`;
    const company = opts.companyId();
    if (company) headers["X-Company-Id"] = company;
    if (method !== "GET") headers["Idempotency-Key"] = crypto.randomUUID();
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(base + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (!res.ok) throw new ApiError((await res.json()) as Problem);
    return (res.status === 204 ? undefined : await res.json()) as T;
  }

  const q = (params: Record<string, string | undefined>) => {
    const s = new URLSearchParams(Object.entries(params).filter((e): e is [string, string] => e[1] !== undefined));
    const text = s.toString();
    return text ? `?${text}` : "";
  };

  return {
    listBranches: (params: { q?: string; sort?: string; cursor?: string; limit?: string } = {}) =>
      call<Page<Branch>>("GET", `/api/v1/iam/branches${q(params)}`),
    createBranch: (companyId: string, input: BranchInput) =>
      call<Branch>("POST", `/api/v1/iam/companies/${encodeURIComponent(companyId)}/branches`, input),
    updateBranch: (id: string, etag: string, patch: BranchInput) =>
      call<Branch>("PATCH", `/api/v1/iam/branches/${encodeURIComponent(id)}`, patch, { "If-Match": etag }),
    archiveBranch: (id: string) => call<Branch>("POST", `/api/v1/iam/branches/${encodeURIComponent(id)}:archive`, {}),
    unarchiveBranch: (id: string) => call<Branch>("POST", `/api/v1/iam/branches/${encodeURIComponent(id)}:unarchive`, {}),
    listExternalAccounts: (params: { q?: string; cursor?: string } = {}) =>
      call<Page<ExternalAccount>>("GET", `/api/v1/iam/external-accounts${q(params)}`),
    inviteExternal: (input: { email: string; portal_kind: ExternalAccount["portal_kind"]; party: { id: string }; employee_id?: string }) =>
      call<ExternalAccount>("POST", "/api/v1/iam/external-accounts", input),
    externalAction: (id: string, action: "suspend" | "reactivate" | "revoke" | "resend", reason?: string) =>
      call<ExternalAccount>("POST", `/api/v1/iam/external-accounts/${encodeURIComponent(id)}:${action}`, reason ? { reason } : {}),
    me: () => call<{ permissions: readonly string[]; field_masks: Record<string, unknown>; companies: readonly string[] }>("GET", "/api/v1/iam/me"),
  };
}

/** Problem codes the Sucursales screen maps to field-level messages (es-CO). */
export const branchErrorMessages: Readonly<Record<string, string>> = {
  branch_code_duplicate: "Ya existe una sucursal con ese código en la empresa.",
  branch_cross_company: "La bodega o unidad de negocio pertenece a otra empresa.",
  branch_in_use: "No se puede archivar: hay bodegas o cajas activas que usan esta sucursal.",
  branch_head_office_required: "Cada empresa debe tener exactamente una sede principal.",
};
