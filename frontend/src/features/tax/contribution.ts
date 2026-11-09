/**
 * P06 contribution to Contabilidad › Impuestos (UI-CONTRACT §14, task-matrix row P06).
 * Registered through the shell's contribution API from P06's own paths; P05's descriptor is not edited.
 * The submenu is visible only with `tax.read` (module-registry.json); every tab also carries its own
 * permission and the API enforces everything (the UI is not a security boundary).
 */

export interface ContributedTab {
  id: string;
  label: string;
  route: string;
  permission: string;
  feature?: string;
  ownerTask: string;
}

export interface ContributedSubmenu {
  module: string;
  id: string;
  label: string;
  route: string;
  permission: string;
  ownerTask: string;
  tabs: ContributedTab[];
}

/** Minimal shape of the P08 shell contribution API that P06 relies on (see CCR P06-1). */
export interface ShellContributionApi {
  contributeSubmenu(submenu: ContributedSubmenu): void;
}

const BASE = "/m/contabilidad/impuestos";

export const TAX_SUBMENU: ContributedSubmenu = {
  module: "contabilidad",
  id: "impuestos",
  label: "Impuestos",
  route: BASE,
  permission: "tax.read",
  ownerTask: "P06",
  tabs: [
    { id: "parametros", label: "Parámetros", route: `${BASE}/parametros`, permission: "tax.parameter.read", ownerTask: "P06" },
    { id: "codigos", label: "Códigos de impuesto", route: `${BASE}/codigos`, permission: "tax.tax_code.read", ownerTask: "P06" },
    { id: "retenciones", label: "Conceptos de retención", route: `${BASE}/retenciones`, permission: "tax.withholding_concept.read", ownerTask: "P06" },
    { id: "ica", label: "Tarifas ICA", route: `${BASE}/ica`, permission: "tax.ica_tariff.read", ownerTask: "P06" },
    { id: "perfiles", label: "Perfiles tributarios", route: `${BASE}/perfiles`, permission: "tax.party_tax_profile.read", ownerTask: "P06" },
    { id: "calendario", label: "Calendario tributario", route: `${BASE}/calendario`, permission: "tax.calendar.read", ownerTask: "P06" },
    { id: "calculo", label: "Calcular impuestos", route: `${BASE}/calculo`, permission: "tax.computation.read", ownerTask: "P06" },
    // The Exógena tab is owned by P17 (module-registry.json); P06 only supplies the format-pack API.
  ],
};

/** Tabs the current principal may see; the submenu disappears entirely without `tax.read`. */
export function visibleTaxSubmenu(permissions: ReadonlySet<string>): ContributedSubmenu | null {
  if (!permissions.has(TAX_SUBMENU.permission)) {
    return null;
  }
  return { ...TAX_SUBMENU, tabs: TAX_SUBMENU.tabs.filter((tab) => permissions.has(tab.permission)) };
}

export function registerTaxContribution(api: ShellContributionApi): void {
  api.contributeSubmenu(TAX_SUBMENU);
}
