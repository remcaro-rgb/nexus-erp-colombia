// Pages of Compras › Solicitudes internas keyed like the compras descriptor ("submenu" / "submenu/tab").
// The compras descriptor (P11a, features/purchasing/module.ts) spreads them into its `pages` — CCR P11b-5 — because
// the shell discovers one descriptor per feature folder (features/*/module.ts).
import { lazy } from "react";
import type { LazyPage } from "@shell/registry";

const page = (name: "MyRequisitionsPage" | "ApprovalInboxPage" | "ConsolidationPage" | "SourcingPage"): LazyPage =>
  lazy(() => import("./RequisitionsPages").then((m) => ({ default: m[name] })));

export const requisitionPages: Record<string, LazyPage> = {
  solicitudes: page("MyRequisitionsPage"),
  "solicitudes/mis-solicitudes": page("MyRequisitionsPage"),
  "solicitudes/por-aprobar": page("ApprovalInboxPage"),
  "solicitudes/consolidacion": page("ConsolidationPage"),
  "solicitudes/abastecimiento": page("SourcingPage"),
};
