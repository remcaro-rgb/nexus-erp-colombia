// DataGrid (owner P08): TanStack Table + fixed-row virtualization (100k rows client-side; server-side paging/sort/filter
// through `onQueryChange` for larger sets), column chooser, saved views (per user; role sharing via the owner API),
// filter builder offering only permitted fields (field masks from /iam/me), inline edit with validation, ARIA grid
// keyboard navigation (arrows, Home/End, Enter edit, Esc cancel, Tab leaves the grid), multi-select bulk actions,
// export hook (P18, policy-applied) and Decimal-safe totals.
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnFiltersState,
  type RowSelectionState,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, Columns3, Download, Filter } from "lucide-react";
import * as RP from "@radix-ui/react-popover";
import { cn } from "@lib/cn";
import { Dec, sumDecimals } from "@lib/money";
import { isFieldVisible, type FieldMasks } from "@lib/permissions";
import { prefKey, readPref, writePref } from "@lib/storage";
import { useT } from "@i18n/index";
import { useFixedVirtual } from "../virtual/useFixedVirtual";
import { formatUnit } from "../components/format";
import { Button } from "../components/Button";

export type GridValueType = "text" | "COP" | "decimal" | "count" | "date";

export interface GridColumn<T> {
  id: keyof T & string;
  header: string;
  type?: GridValueType;
  width?: number;
  editable?: boolean;
  /** Return an error message or null. */
  validate?: (value: string, row: T) => string | null;
  total?: boolean;
  filterable?: boolean;
  render?: (row: T) => ReactNode;
}

export interface GridView {
  name: string;
  visibility: VisibilityState;
  sorting: SortingState;
  filters: ColumnFiltersState;
}

export interface DataGridProps<T extends { id: string }> {
  label: string;
  /** Resource name for field masks (e.g. "sales.invoice"). */
  resource: string;
  rows: T[];
  columns: GridColumn<T>[];
  masks?: FieldMasks;
  userId?: string | null;
  height?: number;
  rowHeight?: number;
  onEdit?: (row: T, column: keyof T & string, value: string) => Promise<void> | void;
  bulkActions?: Array<{ id: string; label: string; run: (rows: T[]) => void }>;
  onExport?: (state: { visible: string[]; sorting: SortingState; filters: ColumnFiltersState }) => void;
  /** Server mode: the grid reports sort/filter changes and shows `rows` as given. */
  onQueryChange?: (q: { sorting: SortingState; filters: ColumnFiltersState }) => void;
  serverSide?: boolean;
}

function display<T>(col: GridColumn<T>, v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  const s = String(v);
  if (col.type === "COP") return formatUnit("COP", s);
  if (col.type === "count") return formatUnit("count", s);
  if (col.type === "decimal") return formatUnit("decimal", s);
  return s;
}

export function DataGrid<T extends { id: string }>(props: DataGridProps<T>) {
  const { label, resource, rows, masks = {}, userId, height = 420, rowHeight = 36, onEdit, bulkActions = [], onExport, onQueryChange, serverSide } = props;
  const t = useT();
  const uid = useId().replace(/:/g, "");
  // Field security: masked columns are removed entirely (not offered in chooser/filter/export).
  const columns = useMemo(() => props.columns.filter((c) => isFieldVisible(masks, resource, c.id)), [props.columns, masks, resource]);
  const viewsKey = prefKey(userId, `grid.views.${resource}`);
  const [views, setViews] = useState<GridView[]>(() => readPref(viewsKey, []));
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filters, setFilters] = useState<ColumnFiltersState>([]);
  const [visibility, setVisibility] = useState<VisibilityState>({});
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [cursor, setCursor] = useState<{ r: number; c: number }>({ r: 0, c: 0 });
  const [editing, setEditing] = useState<{ r: number; c: number; value: string; error: string | null } | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  useEffect(() => onQueryChange?.({ sorting, filters }), [sorting, filters]); // eslint-disable-line react-hooks/exhaustive-deps

  const defs = useMemo<ColumnDef<T>[]>(
    () =>
      columns.map((c) => ({
        id: c.id,
        accessorFn: (r: T) => r[c.id] as unknown,
        header: c.header,
        filterFn: (row, id, value: string) => String(row.getValue(id) ?? "").toLocaleLowerCase("es-CO").includes(String(value).toLocaleLowerCase("es-CO")),
        sortingFn: c.type === "COP" || c.type === "decimal" || c.type === "count" ? (a, b, id) => new Dec(String(a.getValue(id) ?? "0")).comparedTo(String(b.getValue(id) ?? "0")) : "alphanumeric",
      })),
    [columns],
  );
  const table = useReactTable({
    data: rows,
    columns: defs,
    getRowId: (r) => r.id,
    state: { sorting, columnFilters: filters, columnVisibility: visibility, rowSelection: selection },
    onSortingChange: setSorting,
    onColumnFiltersChange: setFilters,
    onColumnVisibilityChange: setVisibility,
    onRowSelectionChange: setSelection,
    enableRowSelection: true,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: serverSide ? undefined : getSortedRowModel(),
    getFilteredRowModel: serverSide ? undefined : getFilteredRowModel(),
    manualSorting: serverSide,
    manualFiltering: serverSide,
  });
  const model = table.getRowModel().rows;
  const visibleCols = columns.filter((c) => visibility[c.id] !== false);
  const virt = useFixedVirtual(model.length, rowHeight, height);
  const selectedRows = table.getSelectedRowModel().rows.map((r) => r.original);
  const totals = useMemo(() => {
    const out: Record<string, string> = {};
    for (const c of visibleCols) if (c.total) out[c.id] = sumDecimals(model.map((r) => (r.original[c.id] as unknown as string) ?? null));
    return out;
  }, [model, visibleCols]);
  const hasTotals = Object.keys(totals).length > 0;
  const colCount = visibleCols.length + 1;

  const cellId = (r: number, c: number) => `${uid}-c-${r}-${c}`;
  const move = (r: number, c: number) => {
    const nr = Math.max(0, Math.min(model.length - 1, r));
    const nc = Math.max(0, Math.min(colCount - 1, c));
    setCursor({ r: nr, c: nc });
    virt.scrollToIndex(nr);
  };

  async function commitEdit() {
    if (!editing) return;
    const row = model[editing.r]?.original;
    const col = visibleCols[editing.c - 1];
    if (!row || !col) return setEditing(null);
    const err = col.validate?.(editing.value, row) ?? null;
    if (err) return setEditing({ ...editing, error: err });
    await onEdit?.(row, col.id, editing.value);
    setEditing(null);
    gridRef.current?.focus();
  }

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (editing) return;
    const { r, c } = cursor;
    switch (e.key) {
      case "ArrowDown":
        move(r + 1, c);
        break;
      case "ArrowUp":
        move(r - 1, c);
        break;
      case "ArrowRight":
        move(r, c + 1);
        break;
      case "ArrowLeft":
        move(r, c - 1);
        break;
      case "Home":
        move(e.ctrlKey ? 0 : r, 0);
        break;
      case "End":
        move(e.ctrlKey ? model.length - 1 : r, colCount - 1);
        break;
      case "PageDown":
        move(r + Math.floor(height / rowHeight), c);
        break;
      case "PageUp":
        move(r - Math.floor(height / rowHeight), c);
        break;
      case " ":
        if (c === 0) model[r]?.toggleSelected();
        break;
      case "Enter":
      case "F2": {
        const col = visibleCols[c - 1];
        const row = model[r]?.original;
        if (col?.editable && row && onEdit) setEditing({ r, c, value: String(row[col.id] ?? ""), error: null });
        break;
      }
      default:
        return;
    }
    e.preventDefault();
  }

  const saveView = () => {
    const name = `${t("grid.views")} ${views.length + 1}`;
    const next = [...views, { name, visibility, sorting, filters }];
    setViews(next);
    writePref(viewsKey, next);
  };

  const ariaSort = (id: string) => {
    const s = sorting.find((x) => x.id === id);
    return s ? (s.desc ? "descending" : "ascending") : "none";
  };

  return (
    <div className="flex flex-col gap-2" data-nx-component="data-grid">
      <div className="flex flex-wrap items-center gap-2">
        <RP.Root>
          <RP.Trigger asChild>
            <Button icon={<Columns3 size={14} />}>{t("grid.columns")}</Button>
          </RP.Trigger>
          <RP.Portal>
            <RP.Content className="z-[50] rounded-card border border-line bg-surface p-3 shadow-xl" sideOffset={6} aria-label={t("grid.columns")}>
              <fieldset className="flex flex-col gap-1">
                <legend className="mb-1 text-xs font-semibold text-muted">{t("grid.columns")}</legend>
                {columns.map((c) => (
                  <label key={c.id} className="flex min-h-6 items-center gap-2 text-sm">
                    <input type="checkbox" checked={visibility[c.id] !== false} onChange={(e) => setVisibility((v) => ({ ...v, [c.id]: e.target.checked }))} />
                    {c.header}
                  </label>
                ))}
              </fieldset>
            </RP.Content>
          </RP.Portal>
        </RP.Root>
        <RP.Root>
          <RP.Trigger asChild>
            <Button icon={<Filter size={14} />}>{`${t("grid.filter")}${filters.length ? ` (${filters.length})` : ""}`}</Button>
          </RP.Trigger>
          <RP.Portal>
            <RP.Content className="z-[50] w-72 rounded-card border border-line bg-surface p-3 shadow-xl" sideOffset={6} aria-label={t("grid.filter")}>
              <div className="flex flex-col gap-2">
                {columns
                  .filter((c) => c.filterable !== false)
                  .map((c) => (
                    <label key={c.id} className="flex flex-col gap-1 text-xs font-semibold text-secondary">
                      {c.header}
                      <input
                        className="min-h-8 rounded-control border border-line bg-input px-2 text-sm text-body"
                        value={String(filters.find((f) => f.id === c.id)?.value ?? "")}
                        onChange={(e) => {
                          const v = e.target.value;
                          setFilters((fs) => [...fs.filter((f) => f.id !== c.id), ...(v ? [{ id: c.id, value: v }] : [])]);
                        }}
                      />
                    </label>
                  ))}
              </div>
            </RP.Content>
          </RP.Portal>
        </RP.Root>
        <Button onClick={saveView}>{t("grid.saveView")}</Button>
        {views.length ? (
          <select
            aria-label={t("grid.views")}
            className="min-h-8 rounded-control border border-line bg-input px-2 text-sm"
            onChange={(e) => {
              const v = views.find((x) => x.name === e.target.value);
              if (v) {
                setVisibility(v.visibility);
                setSorting(v.sorting);
                setFilters(v.filters);
              }
            }}
            defaultValue=""
          >
            <option value="" disabled>
              {t("grid.views")}
            </option>
            {views.map((v) => (
              <option key={v.name} value={v.name}>
                {v.name}
              </option>
            ))}
          </select>
        ) : null}
        {onExport ? (
          <Button icon={<Download size={14} />} onClick={() => onExport({ visible: visibleCols.map((c) => c.id), sorting, filters })}>
            {t("grid.export")}
          </Button>
        ) : null}
        <p role="status" aria-live="polite" className="ml-auto text-xs text-muted">
          {selectedRows.length ? t("grid.selected", { count: selectedRows.length }) : t("grid.rows", { count: model.length })}
        </p>
      </div>
      {selectedRows.length && bulkActions.length ? (
        <div role="toolbar" aria-label={t("grid.selected", { count: selectedRows.length })} className="flex gap-2">
          {bulkActions.map((a) => (
            <Button key={a.id} variant="secondary" onClick={() => a.run(selectedRows)}>
              {a.label}
            </Button>
          ))}
        </div>
      ) : null}
      <div
        ref={gridRef}
        role="grid"
        aria-label={label}
        aria-rowcount={model.length + 1 + (hasTotals ? 1 : 0)}
        aria-colcount={colCount}
        aria-multiselectable="true"
        tabIndex={0}
        aria-activedescendant={editing ? undefined : cellId(cursor.r, cursor.c)}
        onKeyDown={onKey}
        className="flex flex-col rounded-card border border-line bg-surface text-sm"
      >
        <div role="rowgroup">
          <div role="row" aria-rowindex={1} className="flex border-b border-line text-xs font-semibold text-muted">
            <div role="columnheader" aria-colindex={1} className="flex w-10 shrink-0 items-center justify-center">
              <input
                type="checkbox"
                tabIndex={-1}
                aria-label={t("grid.selectAll")}
                checked={table.getIsAllRowsSelected()}
                onChange={table.getToggleAllRowsSelectedHandler()}
              />
            </div>
            {visibleCols.map((c, i) => (
              <div key={c.id} role="columnheader" aria-colindex={i + 2} aria-sort={ariaSort(c.id)} className={cn("flex min-w-0 flex-1 items-center px-3 py-2", c.type && c.type !== "text" && "justify-end")}>
                <button type="button" tabIndex={-1} onClick={() => table.getColumn(c.id)?.toggleSorting()} className="inline-flex min-h-6 items-center gap-1 font-semibold hover:text-fg">
                  {c.header}
                  {sorting[0]?.id === c.id ? sorting[0].desc ? <ArrowDown aria-hidden="true" size={12} /> : <ArrowUp aria-hidden="true" size={12} /> : null}
                </button>
              </div>
            ))}
          </div>
        </div>
        <div ref={virt.ref} onScroll={virt.onScroll} role="rowgroup" className="relative overflow-y-auto" style={{ height }}>
          {model.length === 0 ? <p className="p-6 text-center text-sm text-muted">{t("grid.empty")}</p> : null}
          <div style={{ height: virt.totalHeight, position: "relative" }} role="none">
            <div style={{ transform: `translateY(${virt.offsetTop}px)` }} role="none">
              {model.slice(virt.start, virt.end).map((row, k) => {
                const r = virt.start + k;
                return (
                  <div key={row.id} role="row" aria-rowindex={r + 2} aria-selected={row.getIsSelected()} data-row-id={row.id} className={cn("flex border-b border-line-subtle", row.getIsSelected() && "bg-nav-active")} style={{ height: rowHeight }}>
                    <div id={cellId(r, 0)} role="gridcell" aria-colindex={1} className={cn("flex w-10 shrink-0 items-center justify-center", cursor.r === r && cursor.c === 0 && "outline-2 -outline-offset-2 outline-ring")}>
                      <input type="checkbox" tabIndex={-1} aria-label={t("grid.selectRow", { n: r + 1 })} checked={row.getIsSelected()} onChange={row.getToggleSelectedHandler()} />
                    </div>
                    {visibleCols.map((c, i) => {
                      const ci = i + 1;
                      const active = cursor.r === r && cursor.c === ci;
                      const isEditing = editing && editing.r === r && editing.c === ci;
                      return (
                        <div
                          key={c.id}
                          id={cellId(r, ci)}
                          role="gridcell"
                          aria-colindex={ci + 1}
                          aria-readonly={!c.editable || !onEdit}
                          onClick={() => setCursor({ r, c: ci })}
                          onDoubleClick={() => c.editable && onEdit && setEditing({ r, c: ci, value: String(row.original[c.id] ?? ""), error: null })}
                          className={cn("flex min-w-0 flex-1 items-center truncate px-3", c.type && c.type !== "text" && "justify-end tabular-nums", active && "outline-2 -outline-offset-2 outline-ring")}
                        >
                          {isEditing ? (
                            <span className="flex w-full flex-col">
                              <input
                                autoFocus
                                aria-label={c.header}
                                aria-invalid={editing.error ? true : undefined}
                                aria-describedby={editing.error ? `${uid}-err` : undefined}
                                className="min-h-7 w-full rounded-control border border-brand bg-input px-2 text-sm"
                                value={editing.value}
                                onChange={(e) => setEditing({ ...editing, value: e.target.value, error: null })}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") {
                                    e.preventDefault();
                                    void commitEdit();
                                  } else if (e.key === "Escape") {
                                    e.preventDefault();
                                    setEditing(null);
                                    gridRef.current?.focus();
                                  }
                                  e.stopPropagation();
                                }}
                              />
                            </span>
                          ) : c.render ? (
                            c.render(row.original)
                          ) : (
                            display(c, row.original[c.id])
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
        {hasTotals ? (
          <div role="rowgroup">
            <div role="row" aria-rowindex={model.length + 2} className="flex border-t border-line bg-nav-hover text-sm font-semibold" data-nx-totals="">
              <div role="rowheader" aria-colindex={1} className="flex w-10 shrink-0 items-center justify-center">
                <span className="nx-sr-only">{t("grid.totals")}</span>
              </div>
              {visibleCols.map((c, i) => (
                <div key={c.id} role="gridcell" aria-colindex={i + 2} className={cn("flex min-w-0 flex-1 items-center px-3 py-2", c.type && c.type !== "text" && "justify-end tabular-nums")}>
                  {totals[c.id] !== undefined ? display(c, totals[c.id]) : i === 0 ? t("grid.totals") : ""}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
      {editing?.error ? (
        <p id={`${uid}-err`} role="alert" className="text-xs font-medium text-danger">
          {t("grid.edit.invalid", { msg: editing.error })}
        </p>
      ) : null}
    </div>
  );
}
