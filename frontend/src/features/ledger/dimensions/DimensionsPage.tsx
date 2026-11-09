// Contabilidad › Dimensiones contables (owner P05; UI-CONTRACT §18/§19, DIMENSIONS-ADDENDUM §5, F-ACC-DIM).
// Four deep-linkable tabs (ARIA tablist from P08 Tabs). Each tab shows the same dataset as a table or as a WAI-ARIA tree
// (P08 TreeTable, no module-local tree), with create (Agrupador/Detalle), move and archive/unarchive dialogs.
// Every change goes through the public API with an Idempotency-Key; success is shown only after the API confirms.
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Archive, ArchiveRestore, FolderPlus, MoveRight, Plus } from "lucide-react";
import { ApiError, createApiClient, type ApiClient } from "@lib/api/client";
import { Button } from "@ds/components/Button";
import { Dialog } from "@ds/components/Dialog";
import { Field, Input, Select } from "@ds/components/Field";
import { Tabs } from "@ds/components/Tabs";
import { TreeTable, type TreeNode, type TreeTableView } from "@ds/tree-table";
import { DimensionApi, toTreeNode, type DimensionNodeDto, type UsageDto } from "./api";
import { DIMENSION_TABS, tabById, type DimensionTab } from "./config";
import { errorMessage, msg } from "../messages";

export interface DimensionsPageProps {
  /** Tab id from the route (/m/contabilidad/dimensiones/:tab). */
  tab?: string;
  onTabChange?: (tab: string) => void;
  /** Active company (shell context); sent as X-Company-Id. */
  companyId?: string | null;
  api?: ApiClient;
  /** Permissions of the principal (UI convenience only; the API enforces them). */
  canWrite?: boolean;
}

type DialogState =
  | { kind: "create"; nodeType: "AGRUPADOR" | "DETALLE" }
  | { kind: "move"; node: DimensionNodeDto }
  | { kind: "archive"; node: DimensionNodeDto; usage: UsageDto | null }
  | null;

export function DimensionsPage({ tab, onTabChange, companyId = null, api, canWrite = true }: DimensionsPageProps) {
  const [localTab, setLocalTab] = useState<string>(tab ?? DIMENSION_TABS[0]!.tab);
  const current = tabById(tab ?? localTab);
  const client = useMemo(() => new DimensionApi(api ?? createApiClient(), companyId), [api, companyId]);
  const [nodes, setNodes] = useState<DimensionNodeDto[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [view, setView] = useState<TreeTableView>("tree");
  const [dialog, setDialog] = useState<DialogState>(null);
  const generation = useRef(0);

  const reload = useCallback(async () => {
    const gen = ++generation.current;
    const ctrl = new AbortController();
    setError(null);
    try {
      const rows = await client.list(current, ctrl.signal);
      if (gen === generation.current) setNodes(rows); // drop stale responses (tab/company switch)
    } catch (e) {
      if (gen === generation.current) setError(errorMessage(e));
    }
    return () => ctrl.abort();
  }, [client, current]);

  useEffect(() => {
    setNodes(null);
    void reload();
  }, [reload]);

  const selectTab = (id: string) => {
    setLocalTab(id);
    setStatus("");
    onTabChange?.(id);
  };

  const byId = useMemo(() => new Map((nodes ?? []).map((n) => [n.id, n])), [nodes]);
  const treeNodes = useMemo<TreeNode[]>(() => (nodes ?? []).map(toTreeNode), [nodes]);
  const groups = useMemo(() => (nodes ?? []).filter((n) => n.node_type === "AGRUPADOR" && n.status === "ACTIVE"), [nodes]);

  const openArchive = async (node: DimensionNodeDto) => {
    setDialog({ kind: "archive", node, usage: null });
    try {
      const usage = await client.usage(current, node.id);
      setDialog((d) => (d && d.kind === "archive" && d.node.id === node.id ? { ...d, usage } : d));
    } catch {
      /* the count is informative only; the archive action itself is still checked by the API */
    }
  };

  const unarchive = async (node: DimensionNodeDto) => {
    try {
      await client.unarchive(current, node.id);
      setStatus(msg.unarchived(node.code));
      await reload();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const renderActions = (t: TreeNode) => {
    const node = byId.get(t.id);
    if (!node || !canWrite) return null;
    return (
      <span className="flex items-center gap-1">
        <Button
          variant="ghost"
          className="min-h-6 px-2 text-xs"
          aria-label={msg.moveLabel(node.code)}
          tabIndex={view === "tree" ? -1 : 0}
          onClick={(e) => {
            e.stopPropagation();
            setDialog({ kind: "move", node });
          }}
          icon={<MoveRight aria-hidden="true" size={14} />}
        >
          {msg.move}
        </Button>
        {node.status === "ACTIVE" ? (
          <Button
            variant="ghost"
            className="min-h-6 px-2 text-xs"
            aria-label={msg.archiveLabel(node.code)}
            tabIndex={view === "tree" ? -1 : 0}
            onClick={(e) => {
              e.stopPropagation();
              void openArchive(node);
            }}
            icon={<Archive aria-hidden="true" size={14} />}
          >
            {msg.archive}
          </Button>
        ) : (
          <Button
            variant="ghost"
            className="min-h-6 px-2 text-xs"
            aria-label={msg.unarchiveLabel(node.code)}
            tabIndex={view === "tree" ? -1 : 0}
            onClick={(e) => {
              e.stopPropagation();
              void unarchive(node);
            }}
            icon={<ArchiveRestore aria-hidden="true" size={14} />}
          >
            {msg.unarchive}
          </Button>
        )}
      </span>
    );
  };

  return (
    <section aria-labelledby="dim-title" className="flex flex-col gap-4" data-nx-page="ledger-dimensions">
      <header className="flex flex-wrap items-center gap-3">
        <div className="flex flex-col">
          <h1 id="dim-title" className="text-lg font-bold text-fg">
            {msg.title}
          </h1>
          <p className="text-xs text-muted">{msg.subtitle}</p>
        </div>
        {canWrite ? (
          <div className="ml-auto flex gap-2">
            <Button icon={<FolderPlus aria-hidden="true" size={16} />} onClick={() => setDialog({ kind: "create", nodeType: "AGRUPADOR" })}>
              {msg.newGroup}
            </Button>
            <Button variant="primary" icon={<Plus aria-hidden="true" size={16} />} onClick={() => setDialog({ kind: "create", nodeType: "DETALLE" })}>
              {msg.newLeaf}
            </Button>
          </div>
        ) : null}
      </header>

      <Tabs
        tabs={DIMENSION_TABS.map((d) => ({ id: d.tab, label: d.label }))}
        selected={current.tab}
        onSelect={selectTab}
        label={msg.title}
        idPrefix="dim"
      >
        <div className="flex flex-col gap-3 pt-3">
          <p role="status" aria-live="polite" className="min-h-4 text-xs text-positive" data-nx-dim-status="">
            {status}
          </p>
          {error ? (
            <div role="alert" className="flex items-center gap-3 rounded-card border border-line bg-danger-bg p-3 text-sm text-danger">
              <span>{error}</span>
              <Button className="ml-auto" onClick={() => void reload()}>
                {msg.retry}
              </Button>
            </div>
          ) : null}
          {nodes === null && !error ? (
            <p className="text-sm text-muted" aria-busy="true">
              {msg.loading}
            </p>
          ) : null}
          {nodes !== null && nodes.length === 0 ? <p className="text-sm text-muted">{msg.empty(current.label)}</p> : null}
          {nodes !== null ? (
            <TreeTable
              key={current.key}
              label={current.label}
              nodes={treeNodes}
              view={view}
              onViewChange={setView}
              renderActions={renderActions}
              defaultExpanded={groups.map((g) => g.id)}
            />
          ) : null}
        </div>
      </Tabs>

      {dialog?.kind === "create" ? (
        <CreateDialog
          dim={current}
          nodeType={dialog.nodeType}
          groups={groups}
          onClose={() => setDialog(null)}
          onSubmit={async (body) => {
            const created = await client.create(current, body);
            setStatus(msg.created(created.code));
            setDialog(null);
            await reload();
          }}
        />
      ) : null}
      {dialog?.kind === "move" ? (
        <MoveDialog
          node={dialog.node}
          groups={groups.filter((g) => g.id !== dialog.node.id && !g.path.startsWith(`${dialog.node.path}/`))}
          onClose={() => setDialog(null)}
          onSubmit={async (parentId) => {
            const moved = await client.move(current, dialog.node.id, parentId);
            setStatus(msg.moved(moved.code, moved.path));
            setDialog(null);
            await reload();
          }}
        />
      ) : null}
      {dialog?.kind === "archive" ? (
        <ArchiveDialog
          node={dialog.node}
          usage={dialog.usage}
          onClose={() => setDialog(null)}
          onSubmit={async (reason) => {
            const archived = await client.archive(current, dialog.node.id, reason);
            setStatus(msg.archived(archived.code));
            setDialog(null);
            await reload();
          }}
        />
      ) : null}
    </section>
  );
}

function useSubmit<T>(fn: (v: T) => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const run = async (v: T) => {
    setBusy(true);
    setErr(null);
    setFieldErrors({});
    try {
      await fn(v);
    } catch (e) {
      setErr(errorMessage(e));
      if (e instanceof ApiError) setFieldErrors(e.fieldErrors());
    } finally {
      setBusy(false);
    }
  };
  return { busy, err, fieldErrors, run };
}

function GroupSelect({ id, describedBy, value, onChange, groups }: { id: string; describedBy?: string; value: string; onChange: (v: string) => void; groups: DimensionNodeDto[] }) {
  return (
    <Select id={id} aria-describedby={describedBy} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{msg.rootOption}</option>
      {groups.map((g) => (
        <option key={g.id} value={g.id}>
          {g.path} — {g.description}
        </option>
      ))}
    </Select>
  );
}

function CreateDialog({
  dim,
  nodeType,
  groups,
  onClose,
  onSubmit,
}: {
  dim: DimensionTab;
  nodeType: "AGRUPADOR" | "DETALLE";
  groups: DimensionNodeDto[];
  onClose: () => void;
  onSubmit: (body: { code: string; description: string; node_type: "AGRUPADOR" | "DETALLE"; parent_id: string | null }) => Promise<void>;
}) {
  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const [parent, setParent] = useState("");
  const { busy, err, fieldErrors, run } = useSubmit(onSubmit);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run({ code: code.trim(), description: description.trim(), node_type: nodeType, parent_id: parent || null });
  };
  const title = nodeType === "AGRUPADOR" ? msg.createGroupTitle(dim.singular) : msg.createLeafTitle(dim.singular);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={title} description={msg.createHint}>
      <form onSubmit={submit} className="flex flex-col gap-3" aria-label={title}>
        <Field label={msg.code} required error={fieldErrors["code"]} hint={msg.codeHint}>
          {(p) => <Input id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid} value={code} maxLength={40} required onChange={(e) => setCode(e.target.value)} />}
        </Field>
        <Field label={msg.description} required error={fieldErrors["description"]}>
          {(p) => <Input id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid} value={description} maxLength={200} required onChange={(e) => setDescription(e.target.value)} />}
        </Field>
        <Field label={msg.parent} error={fieldErrors["parent_id"]} hint={msg.parentHint}>
          {(p) => <GroupSelect id={p.id} describedBy={p.describedBy} value={parent} onChange={setParent} groups={groups} />}
        </Field>
        {err ? (
          <p role="alert" className="text-sm text-danger">
            {err}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{msg.cancel}</Button>
          <Button type="submit" variant="primary" loading={busy}>
            {msg.save}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function MoveDialog({
  node,
  groups,
  onClose,
  onSubmit,
}: {
  node: DimensionNodeDto;
  groups: DimensionNodeDto[];
  onClose: () => void;
  onSubmit: (parentId: string | null) => Promise<void>;
}) {
  const [parent, setParent] = useState(node.parent_id ?? "");
  const { busy, err, run } = useSubmit(onSubmit);
  const title = msg.moveTitle(node.code);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={title} description={msg.moveHint}>
      <form
        aria-label={title}
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void run(parent || null);
        }}
      >
        <Field label={msg.newParent}>{(p) => <GroupSelect id={p.id} describedBy={p.describedBy} value={parent} onChange={setParent} groups={groups} />}</Field>
        {err ? (
          <p role="alert" className="text-sm text-danger">
            {err}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{msg.cancel}</Button>
          <Button type="submit" variant="primary" loading={busy}>
            {msg.move}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function ArchiveDialog({
  node,
  usage,
  onClose,
  onSubmit,
}: {
  node: DimensionNodeDto;
  usage: UsageDto | null;
  onClose: () => void;
  onSubmit: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const { busy, err, run } = useSubmit(onSubmit);
  const title = msg.archiveTitle(node.code);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={title} description={msg.archiveHint}>
      <form
        aria-label={title}
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void run(reason.trim());
        }}
      >
        <p className="text-sm text-body" data-nx-usage="">
          {usage === null ? msg.usageLoading : msg.usage(usage.references)}
        </p>
        <Field label={msg.reason}>{(p) => <Input id={p.id} aria-describedby={p.describedBy} value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} />}</Field>
        {err ? (
          <p role="alert" className="text-sm text-danger">
            {err}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{msg.cancel}</Button>
          <Button type="submit" variant="danger" loading={busy}>
            {msg.archive}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
