// IndexedDB wrapper (Dexie) for the POS PWA shell (owner P08; consumer P12).
// * catalog: READ-ONLY cache of catalog snapshots (items/prices) keyed by company, refreshed from the API.
// * drafts: local UNPOSTED drafts. Drafts are never posted from here; posting is always an online API action
//   (C1-12: no write queue for posting commands). Everything is scoped by tenant + company and cleared on logout.
import Dexie, { type Table } from "dexie";

export interface CatalogEntry {
  key: string; // `${tenant}|${company}|${kind}|${id}`
  tenantId: string;
  companyId: string;
  kind: string;
  id: string;
  data: unknown;
  fetchedAt: string;
}

export interface DraftEntry {
  key: string; // `${tenant}|${company}|${docType}|${localId}`
  tenantId: string;
  companyId: string;
  docType: string;
  localId: string;
  payload: unknown;
  updatedAt: string;
  status: "draft";
}

export class NexusOfflineDb extends Dexie {
  catalog!: Table<CatalogEntry, string>;
  drafts!: Table<DraftEntry, string>;

  constructor(name = "nexus-offline") {
    super(name);
    this.version(1).stores({
      catalog: "key, [tenantId+companyId+kind], fetchedAt",
      drafts: "key, [tenantId+companyId+docType], updatedAt",
    });
  }
}

export class OfflineStore {
  constructor(
    private readonly db: NexusOfflineDb,
    private readonly tenantId: string,
    private readonly companyId: string,
  ) {}

  private k(...parts: string[]): string {
    return [this.tenantId, this.companyId, ...parts].join("|");
  }

  /** Replace the cached snapshot of one catalog kind (read-only cache). */
  async putCatalog(kind: string, rows: Array<{ id: string } & Record<string, unknown>>, fetchedAt: string): Promise<void> {
    await this.db.transaction("rw", this.db.catalog, async () => {
      await this.db.catalog.where("[tenantId+companyId+kind]").equals([this.tenantId, this.companyId, kind]).delete();
      await this.db.catalog.bulkPut(
        rows.map((r) => ({ key: this.k(kind, r.id), tenantId: this.tenantId, companyId: this.companyId, kind, id: r.id, data: r, fetchedAt })),
      );
    });
  }

  async listCatalog<T = unknown>(kind: string): Promise<T[]> {
    const rows = await this.db.catalog.where("[tenantId+companyId+kind]").equals([this.tenantId, this.companyId, kind]).toArray();
    return rows.map((r) => r.data as T);
  }

  async saveDraft(docType: string, localId: string, payload: unknown, now = new Date().toISOString()): Promise<void> {
    await this.db.drafts.put({
      key: this.k(docType, localId),
      tenantId: this.tenantId,
      companyId: this.companyId,
      docType,
      localId,
      payload,
      updatedAt: now,
      status: "draft",
    });
  }

  async listDrafts(docType: string): Promise<DraftEntry[]> {
    return this.db.drafts.where("[tenantId+companyId+docType]").equals([this.tenantId, this.companyId, docType]).toArray();
  }

  async deleteDraft(docType: string, localId: string): Promise<void> {
    await this.db.drafts.delete(this.k(docType, localId));
  }

  /** Logout / company switch: wipe everything for this tenant+company. */
  async clearScope(): Promise<void> {
    await this.db.transaction("rw", this.db.catalog, this.db.drafts, async () => {
      await this.db.catalog.filter((r) => r.tenantId === this.tenantId && r.companyId === this.companyId).delete();
      await this.db.drafts.filter((r) => r.tenantId === this.tenantId && r.companyId === this.companyId).delete();
    });
  }
}
