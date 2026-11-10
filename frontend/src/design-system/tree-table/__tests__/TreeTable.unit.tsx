// T-ACC-DIM-12 (P08, unit, gate G1): TreeTable — 10,000 nodes virtualized, ARIA tree keyboard, table/tree same dataset.
import { describe, expect, it } from "vitest";
import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderDs } from "@app/test/render";
import { TreeTable } from "../TreeTable";
import type { TreeNode } from "../model";

/** Synthetic dimension tree: 100 AGRUPADOR roots × 99 DETALLE children = 10,000 nodes (no real data). */
function syntheticTree(groups = 100, perGroup = 99): TreeNode[] {
  const out: TreeNode[] = [];
  for (let g = 0; g < groups; g++) {
    const gid = `g${g}`;
    const gcode = `CC${String(g).padStart(3, "0")}`;
    out.push({ id: gid, parentId: null, code: gcode, description: `Agrupador sintético ${g}`, nodeType: "AGRUPADOR", status: "ACTIVE" });
    for (let d = 0; d < perGroup; d++) {
      out.push({
        id: `${gid}-d${d}`,
        parentId: gid,
        code: `${gcode}-${String(d).padStart(3, "0")}`,
        description: `Detalle sintético ${g}.${d}`,
        nodeType: "DETALLE",
        status: d === 7 ? "ARCHIVED" : "ACTIVE",
      });
    }
  }
  return out;
}

const NODES = syntheticTree();

function status(): string {
  return document.querySelector("[data-nx-tree-status]")?.textContent ?? "";
}
function tree() {
  return screen.getByRole("tree", { name: "Centros de costo" });
}
function active(): HTMLElement {
  const id = tree().getAttribute("aria-activedescendant");
  if (!id) throw new Error("no active descendant");
  const el = document.getElementById(id);
  if (!el) throw new Error(`active descendant ${id} not rendered`);
  return el;
}

describe("T-ACC-DIM-12 TreeTable", () => {
  it("T-ACC-DIM-12 renders 10,000 nodes virtualized in both views", () => {
    expect(NODES).toHaveLength(10_000);
    const t0 = performance.now();
    renderDs(<TreeTable label="Centros de costo" nodes={NODES} defaultView="table" height={480} rowHeight={36} />);
    const elapsed = performance.now() - t0;
    const table = screen.getByRole("table", { name: "Centros de costo" });
    // aria-rowcount announces the full dataset (header + 10,000), but only a window is in the DOM.
    expect(table).toHaveAttribute("aria-rowcount", "10001");
    const rendered = within(table).getAllByRole("row").length - 1;
    expect(rendered).toBeGreaterThan(0);
    expect(rendered).toBeLessThan(60);
    expect(elapsed).toBeLessThan(5_000);

    fireEvent.click(screen.getByRole("button", { name: "Árbol" }));
    const items = within(tree()).getAllByRole("treeitem");
    expect(items.length).toBe(30); // 100 collapsed roots, windowed to viewport + overscan
    expect(tree().getAttribute("data-total-rows")).toBe("100");
  });

  it("T-ACC-DIM-12 virtual window follows scrolling", () => {
    renderDs(<TreeTable label="Centros de costo" nodes={NODES} defaultView="table" height={360} rowHeight={36} />);
    const scroller = document.querySelector('[role="rowgroup"][data-total-rows]') as HTMLElement;
    act(() => {
      scroller.scrollTop = 36 * 5000;
      fireEvent.scroll(scroller);
    });
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    const idx = rows.map((r) => Number(r.getAttribute("aria-rowindex")));
    expect(Math.min(...idx)).toBeGreaterThan(4900);
    expect(Math.max(...idx)).toBeLessThan(5100);
    expect(rows.length).toBeLessThan(60);
  });

  it("T-ACC-DIM-12 WAI-ARIA tree keyboard navigation", async () => {
    const user = userEvent.setup();
    renderDs(<TreeTable label="Centros de costo" nodes={NODES} defaultView="tree" />);
    const t = tree();
    t.focus();
    expect(t).toHaveFocus();
    // First item active; roots are level 1 with posinset/setsize.
    let a = active();
    expect(a).toHaveAttribute("role", "treeitem");
    expect(a).toHaveAttribute("aria-level", "1");
    expect(a).toHaveAttribute("aria-posinset", "1");
    expect(a).toHaveAttribute("aria-setsize", "100");
    expect(a).toHaveAttribute("aria-expanded", "false");
    expect(a.textContent).toContain("CC000");

    await user.keyboard("{ArrowDown}");
    expect(active().textContent).toContain("CC001");
    await user.keyboard("{ArrowUp}");
    expect(active().textContent).toContain("CC000");

    // → expands, → again moves to the first child, ← goes back to the parent, ← collapses.
    await user.keyboard("{ArrowRight}");
    a = active();
    expect(a).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{ArrowRight}");
    a = active();
    expect(a).toHaveAttribute("aria-level", "2");
    expect(a).toHaveAttribute("aria-setsize", "99");
    expect(a.textContent).toContain("CC000-000");
    expect(a).not.toHaveAttribute("aria-expanded"); // leaf
    await user.keyboard("{ArrowLeft}");
    expect(active().textContent).toContain("CC000");
    expect(active()).toHaveAttribute("aria-level", "1");
    await user.keyboard("{ArrowLeft}");
    expect(active()).toHaveAttribute("aria-expanded", "false");

    // End jumps to the last root (scrolled into the virtual window), Home back to the first.
    await user.keyboard("{End}");
    expect(active().textContent).toContain("CC099");
    expect(active()).toHaveAttribute("aria-posinset", "100");
    await user.keyboard("{Home}");
    expect(active().textContent).toContain("CC000");

    // Enter selects/activates; Space selects.
    await user.keyboard("{Enter}");
    expect(active()).toHaveAttribute("aria-selected", "true");

    // Type-ahead jumps to the next code starting with the typed text.
    await user.keyboard("cc05");
    expect(active().textContent).toContain("CC05");
  });

  it("T-ACC-DIM-12 table and tree show the same dataset (search, badges, archived label)", () => {
    renderDs(<TreeTable label="Centros de costo" nodes={NODES} defaultView="table" initialQuery="CC042-007" />);
    // Table view: exactly the matching node, with path to its ancestor and archived text label.
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(1);
    const row = rows[0] as HTMLElement;
    expect(row.getAttribute("data-node-id")).toBe("g42-d7");
    expect(within(row).getByText("Detalle")).toBeInTheDocument();
    expect(within(row).getByText("Archivado")).toBeInTheDocument();
    expect(row.textContent).toContain("CC042");
    expect(status()).toContain("1 coincidencia");

    // Tree view of the same dataset: the match is shown with its ancestor auto-expanded.
    fireEvent.click(screen.getByRole("button", { name: "Árbol" }));
    const items = within(tree()).getAllByRole("treeitem");
    expect(items.map((i) => i.getAttribute("data-node-id"))).toEqual(["g42", "g42-d7"]);
    expect(items[0]).toHaveAttribute("aria-expanded", "true");
    expect(within(items[0] as HTMLElement).getByText("Agrupador")).toBeInTheDocument();
    expect((items[1] as HTMLElement).querySelector("mark")?.textContent).toBe("CC042-007");

    // Clearing the search: both views cover every node of the same dataset.
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    expect(status()).toContain("10.000 coincidencias");
    fireEvent.click(screen.getByRole("button", { name: "Tabla" }));
    expect(screen.getByRole("table")).toHaveAttribute("aria-rowcount", "10001");
  });

  it("T-ACC-DIM-12 sorts and lazily loads children", async () => {
    const user = userEvent.setup();
    const roots: TreeNode[] = [
      { id: "a", parentId: null, code: "B-ROOT", description: "Segundo", nodeType: "AGRUPADOR", status: "ACTIVE", hasChildren: true },
      { id: "b", parentId: null, code: "A-ROOT", description: "Primero", nodeType: "AGRUPADOR", status: "ACTIVE" },
    ];
    const calls: string[] = [];
    const loadChildren = async (id: string): Promise<TreeNode[]> => {
      calls.push(id);
      return [{ id: `${id}-1`, parentId: id, code: "B-ROOT-01", description: "Hijo perezoso", nodeType: "DETALLE", status: "ACTIVE" }];
    };
    renderDs(<TreeTable label="Centros de costo" nodes={roots} defaultView="tree" loadChildren={loadChildren} />);
    const items = () => within(tree()).getAllByRole("treeitem").map((i) => i.getAttribute("data-node-id"));
    expect(items()).toEqual(["b", "a"]); // sorted by code
    tree().focus();
    await user.keyboard("{ArrowDown}{ArrowRight}");
    expect(calls).toEqual(["a"]);
    expect(await screen.findByText("Hijo perezoso")).toBeInTheDocument();
    expect(items()).toEqual(["b", "a", "a-1"]);

    fireEvent.click(screen.getByRole("button", { name: "Tabla" }));
    const header = screen.getByRole("columnheader", { name: "Código" });
    expect(header).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "descending");
    const codes = within(screen.getByRole("table"))
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.getAttribute("data-node-id"));
    expect(codes).toEqual(["a-1", "a", "b"]); // the lazily loaded child is part of the same dataset
  });
});
