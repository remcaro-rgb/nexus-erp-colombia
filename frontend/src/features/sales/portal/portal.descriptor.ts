// Customer portal pages contributed by P10 (part P10b) to the P08 portal shell (discovered via
// features/**/portal.descriptor.ts). "inicio" is the shell's KPI dashboard; the other menus of
// module-registry.json › portals › clientes are here.
import type { PortalDescriptor } from "@shell/portal/registry";
import { InvoicesPage, OrdersPage, PaymentsPage, ProfilePage, QuotesPage, RequestsPage, StatementPage } from "./Pages";

const descriptor: PortalDescriptor = {
  portal: "clientes",
  pages: {
    cotizaciones: QuotesPage,
    pedidos: OrdersPage,
    facturas: InvoicesPage,
    pagos: PaymentsPage,
    "estado-cuenta": StatementPage,
    solicitudes: RequestsPage,
    perfil: ProfilePage,
  },
};

export default descriptor;
