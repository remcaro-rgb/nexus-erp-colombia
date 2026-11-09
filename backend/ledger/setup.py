"""Company bootstrap for the ledger: books, journals, standard account roles.

Idempotent; called by the seed (P21) and by tests. Account codes are never
invented here: the PUC template and the role → account determinations come
from the tenant / P21 synthetic seed.
"""

from __future__ import annotations

import uuid

from kernel.uow import UnitOfWork
from ledger.models import AccountRole, Book, Journal

BOOKS = (("NIIF", "Libro NIIF", True), ("FISCAL", "Libro fiscal", False))
JOURNALS = (
    ("GENERAL", "Comprobante general"),
    ("SALES", "Ventas"),
    ("PURCHASES", "Compras"),
    ("CASH", "Caja"),
    ("BANK", "Bancos"),
    ("PAYROLL", "Nómina"),
    ("INVENTORY", "Inventarios"),
    ("ASSETS", "Activos fijos"),
    ("ADJUSTMENTS", "Ajustes"),
    ("OPENING", "Apertura"),
    ("CLOSING", "Cierre"),
)
ROLES = (
    ("ROUNDING", "Redondeo"),
    ("FX_ROUNDING", "Redondeo por diferencia en cambio"),
    ("AR", "Cuentas por cobrar clientes"),
    ("AP", "Cuentas por pagar proveedores"),
    ("SALES_REVENUE", "Ingresos por ventas"),
    ("INVENTORY", "Inventarios"),
    ("RETAINED_EARNINGS", "Resultados de ejercicios anteriores"),
)


def setup_company(uow: UnitOfWork, company_id: uuid.UUID) -> None:
    ctx = uow.ctx
    ctx.require_company(company_id)
    for code, name, primary in BOOKS:
        Book.objects.get_or_create(
            tenant_id=ctx.tenant_id, company_id=company_id, code=code, defaults={"name": name, "primary": primary}
        )
    for code, name in JOURNALS:
        Journal.objects.get_or_create(
            tenant_id=ctx.tenant_id, company_id=company_id, code=code, defaults={"name": name}
        )
    for code, name in ROLES:
        AccountRole.objects.get_or_create(
            tenant_id=ctx.tenant_id, company_id=company_id, code=code, defaults={"name": name}
        )
