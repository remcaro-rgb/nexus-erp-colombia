// Unit tests for lib helpers (P08): NIT DV, dates, cache keys/context store, offline store, API client.
import { describe, expect, it } from "vitest";
import { computeDv, parseNit, formatNit } from "../nit";
import { formatIsoDate, parseDmy, toDmy, todayBogota } from "../dates";
import { cacheKey, ContextStore, StaleContextError } from "../context/store";
import { NexusOfflineDb, OfflineStore } from "../offline/db";
import { ApiError, buildUrl, createApiClient } from "../api/client";
import { permissionHash, toPermissionSet, can, isFieldVisible } from "../permissions";

describe("P08 lib", () => {
  it("NIT check digit (DIAN modulo 11) and parsing", () => {
    expect(computeDv("800197268")).toBe(4); // published NIT of the tax authority itself
    expect(computeDv("123456789")).toBe(6); // synthetic
    expect(parseNit("123.456.789-6").valid).toBe(true);
    expect(parseNit("123.456.789-1").valid).toBe(false);
    expect(parseNit("1234567896").valid).toBe(true);
    expect(formatNit("123456789", 6)).toBe("123.456.789-6");
  });

  it("dates dd/mm/aaaa and Bogotá display", () => {
    expect(parseDmy("15/11/2026")).toBe("2026-11-15");
    expect(parseDmy("31/02/2026")).toBeNull();
    expect(parseDmy("29/02/2028")).toBe("2028-02-29");
    expect(toDmy("2026-11-15")).toBe("15/11/2026");
    expect(formatIsoDate("2026-11-15")).toBe("15 nov 2026");
    expect(todayBogota(new Date("2026-11-16T03:00:00Z"))).toBe("2026-11-15"); // 22:00 in Bogotá
  });

  it("cache key covers tenant, company, period, permission hash, endpoint and params", () => {
    const ctx = { tenantId: "t", companyId: "c1", periodId: "p1", permissionHash: "h" };
    expect(cacheKey(ctx, "/x", { b: 2, a: 1 })).toEqual(["t", "c1", "p1", "h", "/x", '[["a",1],["b",2]]']);
    expect(permissionHash(toPermissionSet(["b", "a"]))).toBe(permissionHash(toPermissionSet(["a", "b"])));
    expect(can(toPermissionSet([]), "x")).toBe(false);
    expect(can(toPermissionSet(["x"]), null)).toBe(false);
    expect(isFieldVisible({ item: ["unit_cost"] }, "item", "unit_cost")).toBe(false);
  });

  it("context switch aborts in-flight requests, clears the cache and drops stale responses", async () => {
    const store = new ContextStore({ tenantId: "t", companyId: "c1", periodId: "p1", permissionHash: "h" });
    await store.fetch("/k", {}, async () => "c1-value");
    expect(store.peek("/k")).toBe("c1-value");
    let release: (v: string) => void = () => undefined;
    let abortedSignal: AbortSignal | null = null;
    const pending = store.fetch("/k2", {}, (signal) => {
      abortedSignal = signal;
      return new Promise<string>((r) => (release = r));
    });
    store.update({ companyId: "c2" });
    expect(store.size).toBe(0);
    expect((abortedSignal as AbortSignal | null)?.aborted).toBe(true);
    release("late-c1-value");
    await expect(pending).rejects.toBeInstanceOf(StaleContextError);
    expect(store.peek("/k2")).toBeUndefined();
    store.clear();
    expect(store.current.companyId).toBeNull();
  });

  it("offline store keeps a read-only catalog cache and unposted drafts per tenant/company", async () => {
    const db = new NexusOfflineDb(`test-${Math.random()}`);
    const a = new OfflineStore(db, "t", "c1");
    const b = new OfflineStore(db, "t", "c2");
    await a.putCatalog("items", [{ id: "i1", name: "Producto sintético" }], "2026-11-15T10:00:00-05:00");
    await a.saveDraft("pos_ticket", "l1", { lines: [] });
    expect(await a.listCatalog("items")).toEqual([{ id: "i1", name: "Producto sintético" }]);
    expect(await b.listCatalog("items")).toEqual([]);
    const drafts = await a.listDrafts("pos_ticket");
    expect(drafts.map((d) => d.status)).toEqual(["draft"]);
    await a.clearScope();
    expect(await a.listDrafts("pos_ticket")).toEqual([]);
    db.close();
  });

  it("API client sends Idempotency-Key on actions and maps problem+json", async () => {
    const seen: Array<Record<string, string>> = [];
    const api = createApiClient(async (_u, init) => {
      seen.push(init?.headers as Record<string, string>);
      return new Response(JSON.stringify({ type: "x", title: "Inválido", status: 422, code: "validation_failed", trace_id: "tr-9", errors: [{ pointer: "/lines/0/amount", detail: "Monto inválido" }] }), {
        status: 422,
      });
    });
    const err = await api.post("/api/v1/x:post", { body: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("validation_failed");
    expect(err.traceId).toBe("tr-9");
    expect(err.fieldErrors()).toEqual({ "lines/0/amount": "Monto inválido" });
    expect(seen[0]?.["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(buildUrl("/a", { x: 1, y: null, z: "" })).toBe("/a?x=1");
  });
});
