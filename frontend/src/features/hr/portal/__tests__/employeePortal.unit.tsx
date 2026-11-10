// F-PORTAL-EMP (P13a) UI unit tests: model helpers and the employee portal pages over a fake portal API.
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ApiClient, RequestOptions } from "@lib/api/client";
import { PortalProvider } from "@shell/portal/PortalShell";
import descriptor from "../portal.descriptor";
import { CertificatesPage, PayslipsPage, VacationsPage } from "../Pages";
import { dataUpdateChanges, estimateBusinessDays, formatCop, validateVacation } from "../model";

function fakePortalApi(routes: Record<string, unknown>, posts: Array<{ path: string; body: unknown }> = []): ApiClient {
  const base: Record<string, unknown> = {
    "/api/portal/v1/me": { id: "u-1", portal_kind: "employee", display_name: "Empleado Sintético" },
    "/api/portal/v1/context": { company_name: "ALFA S.A.S. (sintética)", menu: { kind: "employee", items: [] }, session_idle_minutes: 30 },
    ...routes,
  };
  return {
    get: async <T,>(path: string) => {
      if (!(path in base)) throw new Error(`unexpected GET ${path}`);
      return base[path] as T;
    },
    post: async <T,>(path: string, opts?: RequestOptions) => {
      posts.push({ path, body: opts?.body });
      return { id: "new" } as T;
    },
    patch: async <T,>() => ({}) as T,
  };
}

describe("F-PORTAL-EMP model", () => {
  it("formats COP decimal strings without floats", () => {
    expect(formatCop({ amount: "2350000.0000", currency: "COP" })).toBe("$ 2.350.000");
    expect(formatCop({ amount: "1234.5", currency: "COP" })).toBe("$ 1.234,50");
    expect(formatCop(null)).toBe("•••");
  });

  it("estimates business days and validates vacation requests against the balance", () => {
    expect(estimateBusinessDays("2026-12-14", "2026-12-20")).toBe(5);
    expect(validateVacation("2026-12-18", "2026-12-14", "10")).toMatch(/anterior/);
    expect(validateVacation("2026-12-19", "2026-12-20", "10")).toMatch(/hábiles/);
    expect(validateVacation("2026-12-14", "2026-12-18", "3")).toMatch(/insuficiente/);
    expect(validateVacation("2026-12-14", "2026-12-18", "42.87")).toBeNull();
  });

  it("sends only allowed, changed contact fields", () => {
    expect(dataUpdateChanges({ email: " a@example.invalid ", phone: "", salary: "1" } as Record<string, string>, { email: "a@example.invalid" })).toEqual({});
    expect(dataUpdateChanges({ phone: "+57 300" }, {})).toEqual({ phone: "+57 300" });
  });

  it("contributes every employee portal menu except the shell dashboard", () => {
    expect(descriptor.portal).toBe("empleados");
    expect(Object.keys(descriptor.pages).sort()).toEqual(["certificados", "datos", "desprendibles", "perfil", "vacaciones"]);
  });
});

describe("F-PORTAL-EMP pages", () => {
  it("lists own payslips with a PDF link per period", async () => {
    const api = fakePortalApi({
      "/api/portal/v1/employee/payslips": {
        items: [{ id: "p-1", status: "POSTED", period: "2026-10", net_pay: { amount: "2350000.0000", currency: "COP" }, version: 1 }],
        next_cursor: null,
      },
    });
    render(<PortalProvider api={api}><PayslipsPage /></PortalProvider>);
    expect(await screen.findByText("$ 2.350.000")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "PDF del periodo 2026-10" })).toHaveAttribute("href", "/api/portal/v1/employee/payslips/p-1/pdf");
  });

  it("labels certificates as synthetic, not legally verified", async () => {
    const api = fakePortalApi({
      "/api/portal/v1/employee/certificates": { items: [{ id: "c-1", status: "AVAILABLE", certificate_type: "INGRESOS_RETENCIONES", year: 2026 }], next_cursor: null },
    });
    render(<PortalProvider api={api}><CertificatesPage /></PortalProvider>);
    expect(await screen.findByText("Certificado de ingresos y retenciones 2026")).toBeInTheDocument();
    expect(document.querySelector('[data-legal-label="certificates"]')?.textContent).toMatch(/NO LEGALMENTE VERIFICADO/);
  });

  it("shows the vacation balance and submits a request", async () => {
    const posts: Array<{ path: string; body: unknown }> = [];
    const api = fakePortalApi(
      {
        "/api/portal/v1/employee/vacations": { items: [{ id: "v-1", start_date: "2026-12-14", end_date: "2026-12-18", days: "5", request_status: "APPROVED" }], next_cursor: null },
        "/api/portal/v1/employee/kpis": { kpis: [{ id: "ep_dias_vacaciones", value: "37.87", previous: null, unit: "days", state: "ok" }] },
      },
      posts,
    );
    render(<PortalProvider api={api}><VacationsPage /></PortalProvider>);
    expect(await screen.findByText("37.87")).toBeInTheDocument();
    expect(screen.getByText("Aprobada")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2027-01-11" } });
    fireEvent.change(screen.getByLabelText("Hasta"), { target: { value: "2027-01-15" } });
    fireEvent.click(screen.getByRole("button", { name: "Solicitar" }));
    await waitFor(() => expect(posts).toEqual([{ path: "/api/portal/v1/employee/vacations", body: { start_date: "2027-01-11", end_date: "2027-01-15" } }]));
    expect(await screen.findByRole("status")).toHaveTextContent("Solicitud enviada");
  });
});
