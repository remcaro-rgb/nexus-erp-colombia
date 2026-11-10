/**
 * P13b contribution to Talento › Nómina (UI-CONTRACT §14, task-matrix row P13): the tab
 * "Conceptos y fórmulas" (F-PAY-FORMULA). The module descriptor of `talento` is P13a's
 * (frontend/src/features/hr/module.ts); this part only contributes its tab through the shell API.
 */

export interface ContributedTab {
  id: string;
  label: string;
  route: string;
  permission: string;
  feature?: string;
  ownerTask: string;
}

export interface ShellTabContributionApi {
  contributeTab(module: string, submenu: string, tab: ContributedTab): void;
}

export const PAYROLL_CONCEPTS_TAB: ContributedTab = {
  id: "conceptos",
  label: "Conceptos y fórmulas",
  route: "/m/talento/nomina/conceptos",
  permission: "payroll.concept.read",
  feature: "F-PAY-FORMULA",
  ownerTask: "P13b",
};

export function visiblePayrollTabs(permissions: ReadonlySet<string>): ContributedTab[] {
  return permissions.has(PAYROLL_CONCEPTS_TAB.permission) ? [PAYROLL_CONCEPTS_TAB] : [];
}

export function registerPayrollContribution(api: ShellTabContributionApi): void {
  api.contributeTab("talento", "nomina", PAYROLL_CONCEPTS_TAB);
}
