// Supplier portal pages contributed by P11 (part P11b) to the P08 portal shell (discovered via
// features/**/portal.descriptor.ts). "inicio" is the shell's KPI dashboard; the other menus of
// module-registry.json › portals › proveedores are here.
import type { PortalDescriptor } from "@shell/portal/registry";
import { InvoicesPage, OrdersPage, PaymentsPage, ProfilePage, RfqsPage, ShipmentsPage } from "./Pages";

const descriptor: PortalDescriptor = {
  portal: "proveedores",
  pages: {
    cotizaciones: RfqsPage,
    ordenes: OrdersPage,
    envios: ShipmentsPage,
    facturas: InvoicesPage,
    pagos: PaymentsPage,
    perfil: ProfilePage,
  },
};

export default descriptor;
