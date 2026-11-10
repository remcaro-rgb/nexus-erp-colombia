// Employee portal pages contributed by P13 to the P08 portal shell (discovered via features/**/portal.descriptor.ts).
// "inicio" is the shell's KPI dashboard; the other menus of module-registry.json › portals › empleados are here.
import type { PortalDescriptor } from "@shell/portal/registry";
import { CertificatesPage, DataUpdatesPage, PayslipsPage, ProfilePage, VacationsPage } from "./Pages";

const descriptor: PortalDescriptor = {
  portal: "empleados",
  pages: {
    desprendibles: PayslipsPage,
    certificados: CertificatesPage,
    vacaciones: VacationsPage,
    datos: DataUpdatesPage,
    perfil: ProfilePage,
  },
};

export default descriptor;
