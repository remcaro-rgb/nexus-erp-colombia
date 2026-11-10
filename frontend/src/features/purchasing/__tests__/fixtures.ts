// Synthetic comparison fixture shared by the purchasing UI tests (SINTÉTICO).
import type { ComparisonDto } from "../api";

export const COMPARISON: ComparisonDto = {
  id: "c1",
  rfq_id: "r1",
  sequence: 1,
  created_at: "2026-11-11T15:00:00Z",
  weights: { price: "60.00", lead_time: "20.00", rating: "10.00", payment_terms: "10.00" },
  ranked: [
    {
      quote_id: "qB", vendor_id: "vB", vendor_code: "PROV-B", vendor_name: "Proveedor B (SINTÉTICO)", received_at: "2026-11-09T15:00:00Z",
      currency: "COP", fx_rate: "1.0000000000", lead_time_days: 3, payment_terms_days: 60, rating: "3.00",
      comparable_total_cop: "495000.0000", lines: [], rank: 1,
      factors: { price: "1.0000", lead_time: "1.0000", rating: "0.6000", payment_terms: "1.0000" },
      points: { price: "60.0000", lead_time: "20.0000", rating: "6.0000", payment_terms: "10.0000" }, score: "96.0000",
    },
    {
      quote_id: "qA", vendor_id: "vA", vendor_code: "PROV-A", vendor_name: "Proveedor A (SINTÉTICO)", received_at: "2026-11-09T15:01:00Z",
      currency: "COP", fx_rate: "1.0000000000", lead_time_days: 5, payment_terms_days: 30, rating: "4.00",
      comparable_total_cop: "500000.0000", lines: [], rank: 2,
      factors: { price: "0.9900", lead_time: "0.6667", rating: "0.8000", payment_terms: "0.5000" },
      points: { price: "59.4000", lead_time: "13.3340", rating: "8.0000", payment_terms: "5.0000" }, score: "85.7340",
    },
  ],
  excluded: [
    {
      quote_id: "qC", vendor_id: "vC", vendor_code: "PROV-C", vendor_name: "Proveedor C (SINTÉTICO)", received_at: "2026-11-12T15:00:00Z",
      currency: "COP", fx_rate: "1.0000000000", lead_time_days: 1, payment_terms_days: 30, rating: "5.00",
      comparable_total_cop: "150000.0000", lines: [], reason: "LATE",
    },
  ],
  recommended_quote_id: "qB",
  tie_rule: null,
  explanation: "Recomendación: PROV-B — Proveedor B (SINTÉTICO) con 96.0000 puntos de 100.",
  warnings: [],
};
