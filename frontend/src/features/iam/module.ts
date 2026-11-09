/**
 * Administración — module descriptor (owner P04; registry entry `administracion`,
 * docs/spec/ui/module-registry.json). The P08 shell loads it through its registry;
 * the server filters modules by permission (unauthorized entries are absent, not disabled).
 *
 * UI permission keys from the registry (`admin.access`, `tenant.admin`, `iam.admin`,
 * `iam.external.admin`, …) are mapped to catalog codes in `permissionCodes`; the API
 * re-checks every call with the same policy (deny by default).
 */

export interface IamSubmenu {
  readonly id: string;
  readonly label: string;
  readonly route: string;
  readonly permission: string;
  readonly feature?: string;
  readonly tabs?: readonly IamSubmenu[];
}

export interface IamKpi {
  readonly id: string;
  readonly label: string;
  readonly unit: "count";
  readonly semantics: "snapshot" | "flow";
  readonly permission: string;
  readonly drill: string;
}

export const permissionCodes: Readonly<Record<string, readonly string[]>> = {
  "admin.access": ["iam.me.read"],
  "tenant.admin": ["iam.company.read", "iam.branch.read"],
  "iam.admin": ["iam.user.read", "iam.role.read", "iam.role_assignment.read", "iam.policy.read"],
  "iam.external.admin": ["iam.external_account.read"],
  "iam.audit": ["iam.event.read"],
};

export const iamModule = {
  id: "administracion",
  label: "Administración",
  icon: "settings",
  group: "Sistema",
  ownerTask: "P04",
  route: "/m/administracion",
  permission: "admin.access",
  submenus: [
    { id: "resumen", label: "Resumen", route: "/m/administracion", permission: "admin.access" },
    {
      id: "empresas",
      label: "Empresas y sucursales",
      route: "/m/administracion/empresas",
      permission: "tenant.admin",
      tabs: [
        { id: "empresas", label: "Empresas", route: "/m/administracion/empresas/lista", permission: "tenant.admin", feature: "F-ACC-BRANCH" },
        { id: "sucursales", label: "Sucursales", route: "/m/administracion/empresas/sucursales", permission: "tenant.admin", feature: "F-ACC-BRANCH" },
      ],
    },
    {
      id: "usuarios",
      label: "Usuarios y roles",
      route: "/m/administracion/usuarios",
      permission: "iam.admin",
      tabs: [
        { id: "internos", label: "Usuarios internos", route: "/m/administracion/usuarios/internos", permission: "iam.admin", feature: "F-PORTAL-CORE" },
        { id: "externos", label: "Usuarios de portales", route: "/m/administracion/usuarios/externos", permission: "iam.external.admin", feature: "F-PORTAL-CORE" },
      ],
    },
    { id: "seguridad", label: "Políticas de seguridad", route: "/m/administracion/seguridad", permission: "iam.admin" },
    { id: "integraciones", label: "Integraciones", route: "/m/administracion/integraciones", permission: "integrations.read" },
  ] satisfies readonly IamSubmenu[],
  dashboard: {
    route: "/m/administracion",
    kpis: [
      { id: "usuarios_activos", label: "Usuarios activos", unit: "count", semantics: "snapshot", permission: "iam.admin", drill: "/m/administracion/usuarios" },
      { id: "sin_mfa", label: "Usuarios sin MFA", unit: "count", semantics: "snapshot", permission: "iam.admin", drill: "/m/administracion/usuarios?mfa=0" },
      { id: "accesos_denegados", label: "Accesos denegados", unit: "count", semantics: "flow", permission: "iam.audit", drill: "/m/aprobaciones/auditoria?tipo=denegado" },
      { id: "roles_personalizados", label: "Roles personalizados", unit: "count", semantics: "snapshot", permission: "iam.admin", drill: "/m/administracion/usuarios" },
    ] satisfies readonly IamKpi[],
  },
  aiPrompts: [
    "¿Qué usuarios no tienen MFA?",
    "Muéstrame las invitaciones de portal vencidas",
    "¿Qué sucursales no tienen bodega asignada?",
  ],
} as const;

export default iamModule;
