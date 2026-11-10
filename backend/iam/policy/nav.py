"""Registry navigation codes → effective catalog permissions (FIX-UI-LOGIN-W03 UL-4, P04).

``docs/spec/ui/module-registry.json`` gates modules, submenus, tabs, quick actions, KPIs and panels with
*navigation codes* (``shell.access``, ``ledger.read``, ``hr.access``, ``inventory.cost.read`` …). They are not
catalog codes (grammar ``<module>.<resource>.<action>``), so each one is resolved here against the
principal's **effective catalog permissions** (``PolicyEngine.effective(ctx)`` over ``catalog_codes()``).
Deny by default; the rules, in order:

1. ``shell.access`` → any authenticated INTERNAL principal (or an AI/MCP principal acting for one) with an
   active company: the shell itself. Dashboard widgets still check their own codes.
2. An explicit row of :data:`NAV_ALIASES` → allowed iff an allowed catalog code matches one of its patterns
   (fnmatch). Covers non-grammar actions (``access``, ``write``, ``close`` …), module aliases (``admin``,
   ``tenant``, ``audit``, ``party``, ``receivables`` …) and codes whose grammar reading would be wrong.
3. ``<m>.<a>`` with a catalog action → allowed iff some allowed catalog code is ``<m>.<resource>.<a>``.
4. ``<m>.<r>.<a>`` with a catalog action → that catalog code when the catalog has it, else any allowed
   ``<m>.<r>….<a>`` (resource prefix: ``inventory.label.read`` → ``inventory.label_template.read``).
5. Anything else → denied.

A module of a later task becomes visible on its own once its catalog codes exist and a role grants them
(rules 3/4 need no edit here). ``/api/v1/iam/me`` and ``/api/v1/ui/context`` return
``effective catalog codes ∪ resolved navigation codes``, so the client's ``can()`` keeps working.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from fnmatch import fnmatchcase
from typing import Any

#: Catalog grammar actions (``contracts/permissions/catalog.yaml › grammar.actions``).
CATALOG_ACTIONS = frozenset({"read", "create", "update", "delete", "approve", "post", "void", "export", "admin"})
SHELL_CODE = "shell.access"


def _write(module: str) -> tuple[str, ...]:
    return (f"{module}.*.create", f"{module}.*.update")


#: Registry navigation code → catalog patterns (allowed iff any allowed catalog code matches one).
NAV_ALIASES: dict[str, tuple[str, ...]] = {
    # Administración: managing users, roles or assignments (the catalog has no generic iam admin action;
    # ``iam.break_glass.admin`` alone would hide the module from the administrador role, which denies it).
    "admin.access": ("iam.*.admin", "iam.user.update", "iam.role.update", "iam.role_assignment.create"),
    # Same rule for the Administración submenus/KPIs gated by ``iam.admin``.
    "iam.admin": ("iam.*.admin", "iam.user.update", "iam.role.update", "iam.role_assignment.create"),
    # Portal users are external accounts (F-PORTAL-CORE): invite, change or revoke them.
    "iam.external.admin": ("iam.external_account.create", "iam.external_account.update", "iam.external_account.void"),
    # "Accesos denegados" KPI reads the IAM security event log.
    "iam.audit": ("iam.event.read",),
    # "Empresas y sucursales": companies are IAM rows; module alias ``tenant`` has no catalog module.
    "tenant.admin": ("iam.company.create", "iam.company.update"),
    # MCP connectors act through delegated grants (iam.delegated_grant); administering them = managing grants.
    "mcp.admin": ("iam.delegated_grant.create", "iam.delegated_grant.update"),
    # No integrations catalog module yet: deny until one exists (pattern reserved for it).
    "integrations.read": ("kernel.integration*.read",),
    # Activity / audit trail: any module's audit resource (today ``workflow.audit_event.read``).
    "audit.read": ("*.audit.read", "*.audit_*.read"),
    # Scheduled jobs live in the workflow module (P07).
    "scheduler.read": ("workflow.scheduled_job.read",),
    # Terceros are the sales party master (shared by sales and purchasing).
    "party.read": ("sales.party.read",),
    # Cartera (receivables) is the sales AR subledger.
    "receivables.read": ("sales.ar_*.read",),
    # Cartera KPIs: no catalog code yet (deny until the provider task adds one).
    "receivables.kpi.read": ("sales.ar_kpi*.read",),
    # Cuentas por pagar (payables) is the purchasing AP subledger and vendor invoices.
    "payables.read": ("purchasing.ap_*.read", "purchasing.vendor_invoice.read"),
    # CxP KPIs: no catalog code yet (deny until the provider task adds one).
    "payables.kpi.read": ("purchasing.ap_kpi*.read",),
    # Talento humano y nómina: any HR, timekeeping or payroll read (non-HR roles get no module).
    "hr.access": ("hr.*.read", "timekeeping.*.read", "payroll.*.read"),
    # Payroll amounts are their own sensitive resource; payroll reports show them.
    "payroll.amounts.read": ("payroll.amount*.read", "payroll.payroll_report.read"),
    # Inventory cost = valuation layers (sensitive unit_cost).
    "inventory.cost.read": ("inventory.cost*.read", "inventory.valuation.read"),
    # "Movimientos" lists stock moves and transfers.
    "inventory.move": ("inventory.move.read", "inventory.transfer.read"),
    # Closing a period = approving/updating ledger periods or the year-end close.
    "ledger.close": ("ledger.period.approve", "ledger.period.update", "ledger.year_end_close.update"),
    # Bank reconciliation = maintaining reconciliation work (rules today).
    "treasury.reconcile": ("treasury.reconciliation*.create", "treasury.reconciliation*.update"),
    # "Nuevo pago" creates/posts a payment batch.
    "treasury.pay": ("treasury.payment_batch.create", "treasury.payment_batch.post"),
    # Depreciation runs are created and posted.
    "assets.depreciate": ("assets.depreciation_run.create", "assets.depreciation_run.post"),
    # "Vender" / "Abrir caja": selling = creating POS tickets.
    "pos.sell": ("pos.ticket.create",),
    # Cash-count differences: supervisors close sessions and post returns.
    "pos.supervise": ("pos.session.update", "pos.return.post"),
    # Goods receipts against purchase orders.
    "purchasing.receive": ("purchasing.receipt.create",),
    # Consolidating requisitions changes them.
    "purchasing.requisition.consolidate": ("purchasing.requisition.update",),
    # Sourcing a requisition opens RFQs.
    "purchasing.requisition.source": ("purchasing.rfq.create",),
    # Financial statements and auxiliaries are standard reports.
    "reporting.financial": ("reporting.standard_report.read",),
    # "Importar": creating an import job.
    "dataio.import": ("dataio.import.create",),
    # ``<m>.write`` (quick actions "Nuevo …"): create or update anything in the module.
    "assets.write": _write("assets"),
    "crm.write": _write("crm"),
    "hr.write": _write("hr"),
    "inventory.write": _write("inventory"),
    "onboarding.write": _write("onboarding"),
    "purchasing.write": _write("purchasing"),
    "reporting.write": _write("reporting"),
}


def registry_codes(registry: Any) -> set[str]:
    """Every ``permission`` string of a registry document (modules, submenus, tabs, quick actions, KPIs, panels)."""
    return {code for code in _walk(registry.get("modules", []) if isinstance(registry, dict) else [])}


def _walk(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "permission" and isinstance(v, str):
                yield v
            else:
                yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def rule_for(code: str, catalog_modules: Iterable[str]) -> str | None:
    """Which rule resolves ``code`` (``shell``, ``alias``, ``module_action``, ``resource``) or ``None``."""
    if code == SHELL_CODE:
        return "shell"
    if code in NAV_ALIASES:
        return "alias"
    parts = code.split(".")
    modules = set(catalog_modules)
    if len(parts) == 2 and parts[1] in CATALOG_ACTIONS and parts[0] in modules:
        return "module_action"
    if len(parts) == 3 and parts[2] in CATALOG_ACTIONS and parts[0] in modules:
        return "resource"
    return None


def resolve(code: str, allowed: frozenset[str], catalog: frozenset[str], *, shell_ok: bool) -> bool:
    """Is navigation code ``code`` allowed for a principal whose allowed catalog codes are ``allowed``?"""
    if code == SHELL_CODE:
        return shell_ok
    patterns = NAV_ALIASES.get(code)
    if patterns is not None:
        return any(fnmatchcase(c, p) for c in allowed for p in patterns)
    parts = code.split(".")
    if len(parts) == 2 and parts[1] in CATALOG_ACTIONS:
        module, action = parts
        return any(c.startswith(f"{module}.") and c.endswith(f".{action}") and c.count(".") == 2 for c in allowed)
    if len(parts) == 3 and parts[2] in CATALOG_ACTIONS:
        if code in catalog:
            return code in allowed
        module, resource, action = parts
        for c in allowed:
            m, _, rest = c.partition(".")
            r, _, a = rest.partition(".")
            if m == module and a == action and r.startswith(resource):
                return True
        return False
    return False


def effective_codes(allowed: Iterable[str], catalog: Iterable[str], registry: Any, *, shell_ok: bool) -> list[str]:
    """``allowed catalog codes ∪ resolved navigation codes`` (sorted)."""
    allowed_set = frozenset(allowed)
    catalog_set = frozenset(catalog)
    nav = {c for c in registry_codes(registry) if resolve(c, allowed_set, catalog_set, shell_ok=shell_ok)}
    return sorted(allowed_set | nav)


def filter_registry(registry: dict[str, Any], can: Callable[[str], bool]) -> dict[str, Any]:
    """``{groups, modules}`` in registry order with every unauthorized entry absent (not disabled).

    A module needs its own permission; inside it, every list entry carrying a ``permission`` (submenus, tabs,
    KPIs, panels, …) is dropped without it and a single object carrying one (the quick action) becomes ``null``.
    """
    modules = [
        _prune(m, can) for m in registry.get("modules", []) if isinstance(m, dict) and can(str(m.get("permission")))
    ]
    return {"groups": list(registry.get("groups", [])), "modules": modules}


def _prune(value: Any, can: Callable[[str], bool]) -> Any:
    if isinstance(value, list):
        return [
            _prune(v, can)
            for v in value
            if not (isinstance(v, dict) and "permission" in v and not can(str(v["permission"])))
        ]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(v, dict) and "permission" in v and not can(str(v["permission"])):
                out[k] = None
            else:
                out[k] = _prune(v, can)
        return out
    return value
