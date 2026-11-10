import { describe, expect, it } from "vitest";
import { contingencyMessage, failedCases, formatCountdown, isSecretRef, statusFilterFromQuery, statusText, statusTone } from "../model";

describe("facturacion view model", () => {
  it("labels simulated statuses with 'Simulado' and never relies on colour alone", () => {
    expect(statusText({ status: "accepted", synthetic: true })).toBe("Validado · Simulado");
    expect(statusText({ status: "rejected", synthetic: false })).toBe("Rechazado");
    expect(statusTone("rejected")).toBe("danger");
    expect(statusTone("contingency_issued")).toBe("warning");
  });

  it("formats the 48 h contingency countdown", () => {
    expect(formatCountdown(null)).toBeNull();
    expect(formatCountdown(0)).toBe("Plazo vencido");
    expect(formatCountdown(3600 * 47 + 60 * 5)).toBe("1 d 23 h 05 min");
    expect(formatCountdown(125)).toBe("00 h 02 min");
  });

  it("builds the contingency banner (active, pending with deadline, overdue)", () => {
    const base = { kind: "ISSUER_OUTAGE", since: null, restored_at: null, deadline_at: null, remaining_seconds: null, overdue: false, pending_documents: 0 };
    expect(contingencyMessage({ ...base, active: true })?.text).toContain("Contingencia activa");
    expect(contingencyMessage({ ...base, active: false })).toBeNull();
    const pending = { ...base, active: false, deadline_at: "2026-11-05T15:00:00Z", remaining_seconds: 7200, pending_documents: 2 };
    expect(contingencyMessage(pending)?.text).toContain("02 h 00 min");
    expect(contingencyMessage({ ...pending, overdue: true, remaining_seconds: 0 })?.tone).toBe("danger");
  });

  it("accepts only secret references for provider credentials (T-PT-OPEN-02 UI side)", () => {
    expect(isSecretRef("secret://einvoice/pt-credenciales")).toBe(true);
    expect(isSecretRef("pt-sandbox-token-123456")).toBe(false);
    expect(isSecretRef("secret://other/x")).toBe(false);
  });

  it("reads the dashboard drill filter and failed contract cases", () => {
    expect(statusFilterFromQuery("?estado=validado")).toBe("validado");
    expect(statusFilterFromQuery("?estado=<script>")).toBeNull();
    expect(failedCases({ adapter: "x", suite_version: "1", passed: false, cases: [{ case: "a", passed: true, detail: "" }, { case: "b", passed: false, detail: "" }] })).toEqual(["b"]);
  });
});
