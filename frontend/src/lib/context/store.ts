// Client context + cache (owner P08), UI-CONTRACT §11:
// cache key = [tenant, company_id, period_id, permission_hash, endpoint, params]; everything is cleared on company
// switch, period switch, logout and permission change; in-flight requests are aborted and responses whose context
// differs from the current one are dropped, so the old company's data never shows while the new request is pending.
export interface ClientContext {
  tenantId: string;
  companyId: string | null;
  periodId: string | null;
  permissionHash: string;
}

export type CacheKey = readonly [string, string | null, string | null, string, string, string];

export function cacheKey(ctx: ClientContext, endpoint: string, params: Record<string, unknown> = {}): CacheKey {
  const p = JSON.stringify(Object.keys(params).sort().map((k) => [k, params[k]]));
  return [ctx.tenantId, ctx.companyId, ctx.periodId, ctx.permissionHash, endpoint, p] as const;
}

function sameContext(a: ClientContext, b: ClientContext): boolean {
  return a.tenantId === b.tenantId && a.companyId === b.companyId && a.periodId === b.periodId && a.permissionHash === b.permissionHash;
}

type Listener = (ctx: ClientContext) => void;

export class StaleContextError extends Error {
  constructor() {
    super("response dropped: context changed while the request was in flight");
    this.name = "StaleContextError";
  }
}

export class ContextStore {
  private ctx: ClientContext;
  private generation = 0;
  private controller = new AbortController();
  private cache = new Map<string, unknown>();
  private listeners = new Set<Listener>();

  constructor(initial: ClientContext) {
    this.ctx = initial;
  }

  get current(): ClientContext {
    return this.ctx;
  }

  get signal(): AbortSignal {
    return this.controller.signal;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Switch company/period/permissions: abort in-flight, clear cache, notify. */
  update(patch: Partial<ClientContext>): void {
    const next = { ...this.ctx, ...patch };
    if (sameContext(next, this.ctx)) return;
    this.reset(next);
  }

  /** Logout: clear everything. */
  clear(): void {
    this.reset({ ...this.ctx, companyId: null, periodId: null, permissionHash: "" });
  }

  private reset(next: ClientContext): void {
    this.controller.abort();
    this.controller = new AbortController();
    this.generation += 1;
    this.cache.clear();
    this.ctx = next;
    for (const l of this.listeners) l(next);
  }

  /** Cached, context-guarded fetch. */
  async fetch<T>(endpoint: string, params: Record<string, unknown>, loader: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const key = JSON.stringify(cacheKey(this.ctx, endpoint, params));
    if (this.cache.has(key)) return this.cache.get(key) as T;
    const gen = this.generation;
    const value = await loader(this.controller.signal);
    if (gen !== this.generation) throw new StaleContextError();
    this.cache.set(key, value);
    return value;
  }

  peek<T>(endpoint: string, params: Record<string, unknown> = {}): T | undefined {
    return this.cache.get(JSON.stringify(cacheKey(this.ctx, endpoint, params))) as T | undefined;
  }

  get size(): number {
    return this.cache.size;
  }
}
