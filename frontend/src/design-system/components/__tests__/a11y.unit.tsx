// T-P08-01 (jsdom subset): axe-core on the design-system components and the full shell — 0 serious/critical.
// Colour contrast is verified separately against tokens (jsdom cannot compute layout colours).
import { describe, expect, it } from "vitest";
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { createMemoryRouter, RouterProvider } from "react-router";
import { renderDs } from "@app/test/render";
import { buildRoutes } from "@app/App";
import { fakeApi } from "@app/test/fakeApi";
import { createApiClient } from "@lib/api/client";
import { DesignSystemProvider } from "@ds/DesignSystemProvider";
import { tokens } from "@ds/tokens";
import { Button, IconButton } from "../Button";
import { Field, Input, Select } from "../Field";
import { MoneyInput, QuantityInput } from "../MoneyInput";
import { DatePicker } from "../DatePicker";
import { PartyPicker } from "../PartyPicker";
import { Badge, FiscalBadge, PostingBadge } from "../Badge";
import { EmptyState, Skeleton, Stepper, Timeline } from "../Feedback";
import { FileDrop } from "../FileDrop";
import { KpiCard } from "../KpiCard";
import { ChartPanel } from "../ChartPanel";
import { FeedPanel } from "../FeedPanel";
import { Tabs } from "../Tabs";

async function serious(container: Element) {
  const res = await axe.run(container, { resultTypes: ["violations"], rules: { "color-contrast": { enabled: false } } });
  return res.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
}

function Gallery() {
  const [money, setMoney] = useState("1234567.89");
  const [qty, setQty] = useState("2");
  const [date, setDate] = useState("2026-11-15");
  const [tab, setTab] = useState("a");
  return (
    <main>
      <h1>Galería</h1>
      <Button variant="primary">Guardar</Button>
      <IconButton label="Cerrar" icon={<span>x</span>} />
      <Field label="Nombre">{(p) => <Input id={p.id} aria-describedby={p.describedBy} />}</Field>
      <Field label="Tipo">{(p) => <Select id={p.id}><option>Uno</option></Select>}</Field>
      <Field label="Valor" hint="Formato: 1.234.567,89">{(p) => <MoneyInput id={p.id} value={money} onValueChange={setMoney} aria-describedby={p.describedBy} />}</Field>
      <Field label="Cantidad">{(p) => <QuantityInput id={p.id} value={qty} onValueChange={setQty} uom="UND" uomLabel="Unidad de medida" />}</Field>
      <Field label="Fecha">{(p) => <DatePicker id={p.id} value={date} onValueChange={setDate} calendarLabel="Abrir calendario" placeholderText="dd/mm/aaaa" />}</Field>
      <PartyPicker value={null} onChange={() => undefined} search={async () => []} />
      <Badge>Neutro</Badge>
      <PostingBadge status="posted" />
      <FiscalBadge status="simulated" />
      <EmptyState title="Sin datos" />
      <Skeleton label="Cargando" className="h-4" />
      <Stepper steps={[{ id: "1", label: "Datos" }, { id: "2", label: "Confirmar" }]} current={0} />
      <Timeline items={[{ id: "1", title: "Creado", at: "15 nov 2026" }]} />
      <FileDrop label="Adjuntos" onFiles={() => undefined} />
      <KpiCard def={{ id: "k", label: "Ventas netas", formula: "Σ clase 41", unit: "COP", permission: "x", comparison: "previous_period" }} value={{ id: "k", value: "1284500", previous: "1200000", delta: "7.04", unit: "COP", state: "ok" }} />
      <ChartPanel title="Ingresos vs. gastos" data={{ state: "ok", series: [{ id: "i", label: "Ingresos" }], points: [{ label: "nov", values: { i: "10" } }] }} />
      <FeedPanel items={[{ id: "1", title: "Comprobante contabilizado" }]} />
      <Tabs tabs={[{ id: "a", label: "A" }, { id: "b", label: "B" }]} selected={tab} onSelect={setTab} label="Pestañas" idPrefix="g">
        <p>Contenido</p>
      </Tabs>
    </main>
  );
}

function luminance(hex: string): number {
  const c = hex.replace("#", "");
  const ch = [0, 2, 4].map((i) => Number.parseInt(c.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (ch[0] as number) + 0.7152 * (ch[1] as number) + 0.0722 * (ch[2] as number);
}
const contrast = (a: string, b: string) => {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m) as [number, number];
  return (x + 0.05) / (y + 0.05);
};

describe("T-P08-01 accessibility", () => {
  it("T-P08-01 design-system components: axe 0 serious/critical", async () => {
    const { container } = renderDs(<Gallery />);
    expect(await serious(container)).toEqual([]);
  });

  it("T-P08-01 full internal shell and portal shell: axe 0 serious/critical", async () => {
    window.innerWidth = 1440;
    for (const path of ["/m/inicio", "/m/contabilidad/dimensiones", "/portal/clientes"]) {
      const api = fakeApi({ portalKind: "customer" });
      const router = createMemoryRouter(buildRoutes(createApiClient(api.fetcher)), { initialEntries: [path] });
      const { container, unmount } = render(
        <DesignSystemProvider theme="light">
          <RouterProvider router={router} />
        </DesignSystemProvider>,
      );
      await waitFor(() => expect(container.querySelector("[data-nx-shell]")).not.toBeNull());
      await waitFor(() => expect(container.querySelector('[data-state="loading"]')).toBeNull());
      expect(await serious(document.body), path).toEqual([]);
      unmount();
    }
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("T-P08-01 token text colours meet 4.5:1 on their surfaces (light and dark)", () => {
    for (const theme of ["light", "dark"] as const) {
      const c = tokens.color[theme];
      for (const fg of ["text.primary", "text.body", "text.secondary", "text.muted", "brand.primary", "positive.text", "danger.text"]) {
        for (const bg of ["surface", "app.bg"]) {
          expect(contrast(c[fg] as string, c[bg] as string), `${theme} ${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
        }
      }
    }
  });
});
