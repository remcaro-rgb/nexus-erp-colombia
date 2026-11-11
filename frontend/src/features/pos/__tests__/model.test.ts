import { describe, expect, it } from "vitest";
import {
  DRAFT_WATERMARK,
  OFFLINE_BANNER,
  addScan,
  checkoutParameters,
  draftSubtotal,
  newDraft,
  posActions,
  priceChangeFromProblem,
  receiptFor,
  resolveScan,
  setQuantity,
  type CatalogItem,
} from "../model";

const catalog: CatalogItem[] = [
  {
    id: "item-soda",
    code: "GAS-350",
    name: "Gaseosa 350 ml (SINTÉTICO)",
    uom: "UND",
    gtin: "7701234567897",
    tax_code: "IVA19",
    price: "2500.000000",
    presentations: [
      { id: "pres-box", code: "CJ12", name: "Caja x12", factor: "12.000000", barcode: "2001234567893", precision: 0 },
      { id: "pres-six", code: "SIX", name: "Six pack", factor: "6.000000", barcode: "2009876543213", precision: 0 },
    ],
  },
];

describe("POS scan input (P12)", () => {
  it("T_INV_BARCODE_05 scan input adds the right presentation line", () => {
    let cart = newDraft("c-1", "s-1", "2026-11-09T15:00:00Z");
    cart = addScan(cart, resolveScan(catalog, "2001234567893\r\n")!);
    cart = addScan(cart, resolveScan(catalog, " 2009876543213")!);
    cart = addScan(cart, resolveScan(catalog, "2001234567893")!); // same presentation → quantity 2
    cart = addScan(cart, resolveScan(catalog, "07701234567897")!); // GTIN-14 spelling of the item → loose unit
    expect(cart.lines.map((l) => [l.presentation_id, l.factor, l.quantity, l.unit_price])).toEqual([
      ["pres-box", "12", "2", "30000.00"],
      ["pres-six", "6", "1", "15000.00"],
      [null, "1", "1", "2500.00"],
    ]);
    expect(cart.lines[0]?.description).toBe("Gaseosa 350 ml (SINTÉTICO) — Caja x12");
    expect(resolveScan(catalog, "999")).toBeNull();
    const body = checkoutParameters(cart, [{ type: "CASH", amount: "77500" }]);
    expect(body.client_uuid).toBe("c-1");
    expect(body.lines[0]).toEqual({ barcode: "2001234567893", quantity: "2", unit_price: "30000.00" });
    expect(body.lines[2]).toEqual({ item_id: "item-soda", quantity: "1", unit_price: "2500.00" });
    expect(draftSubtotal(cart)).toBe("77500.00");
    expect(setQuantity(cart, cart.lines[1]?.key ?? "", "0").lines).toHaveLength(2);
  });
});

describe("ADR-015 online finalization (P12)", () => {
  it("T_P12_04 disconnected: checkout, tenders, returns, cash movements, close and print are disabled", () => {
    const off = posActions("offline", { sessionOpen: true, committedReceipt: true });
    expect(off).toEqual({
      checkout: false,
      tender: false,
      printReceipt: false,
      returns: false,
      cashMovements: false,
      closeSession: false,
      buildCart: true,
      parkDraft: true,
    });
    const on = posActions("online", { sessionOpen: true, committedReceipt: false });
    expect(on.checkout && on.tender && on.returns && on.cashMovements && on.closeSession).toBe(true);
    expect(on.printReceipt).toBe(false); // nothing committed yet → no receipt
    expect(OFFLINE_BANNER).toBe("Sin conexión: no se pueden finalizar ventas");
    expect(DRAFT_WATERMARK).toBe("BORRADOR – sin valor fiscal");
  });

  it("T_P12_11 the receipt exists only for the committed server ticket", () => {
    expect(receiptFor(null)).toBeNull();
    expect(receiptFor({ id: "t", number: "", status: "DRAFT", total: null, lines: [] })).toBeNull();
    const r = receiptFor({
      id: "t",
      number: "SETP1",
      status: "POSTED",
      total: { amount: "35700.0000", currency: "COP" },
      change_due: { amount: "4300.0000", currency: "COP" },
      cude: null,
      lines: [{ line_no: 1, description: "Gaseosa — Caja x12", quantity: { value: "1" }, amount: { amount: "30000.0000" } }],
    });
    expect(r?.number).toBe("SETP1");
    expect(r?.total).toBe("35700.0000");
    expect(r?.note).toContain("CUDE pendiente");
    expect(receiptFor({ id: "t", number: "SETP1", status: "POSTED", total: null, lines: [] }, { copy: true })?.copy).toBe(true);
  });

  it("T_P12_05 a server price change is surfaced for confirmation", () => {
    const pc = priceChangeFromProblem({
      code: "pos_price_changed",
      errors: { server: { total: "6188.00", diff: [{ line_no: 1, item_id: "i", draft_unit_price: "2500", server_unit_price: "2600.00" }] } },
    });
    expect(pc).toEqual({ total: "6188.00", diff: [{ line_no: 1, item_id: "i", draft_unit_price: "2500", server_unit_price: "2600.00" }] });
    expect(priceChangeFromProblem({ code: "pos_tender_mismatch" })).toBeNull();
    const body = checkoutParameters(newDraft("c-2", "s"), [], pc!.total);
    expect(body.expected_total).toBe("6188.00");
  });
});
