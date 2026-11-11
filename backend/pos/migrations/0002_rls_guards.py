"""Row-level security, company/branch composite FKs and immutability guards of the POS tables (ADR-003, C1-5).

* ENABLE + FORCE RLS with the tenant + company predicate on every table (unbound sessions see no rows).
* ``(tenant_id, company_id) → iam_company`` on every table; ``PosConfig.branch_id`` →
  ``iam_branch (tenant_id, company_id, id)``: a POS of company A can never point to a branch of company B
  (F-ACC-BRANCH, T-ACC-BRANCH-04).
* Posted tickets, lines, tenders, returns, return lines and cash movements are immutable (corrections by returns
  or reversal movements, never by update or delete).
"""

from django.db import migrations

from iam import rls
from iam.branches.db import branch_fk
from kernel.db import company_fk_sql, immutable_trigger

TABLES = [
    "pos_config",
    "pos_terminal",
    "pos_session",
    "pos_ticket",
    "pos_ticket_line",
    "pos_tender",
    "pos_cash_movement",
    "pos_return",
    "pos_return_line",
    "pos_invoice_request",
]
POSTED = ["pos_ticket", "pos_ticket_line", "pos_tender", "pos_cash_movement", "pos_return", "pos_return_line"]


def _company_fk(table: str) -> migrations.RunSQL:
    forward, reverse = company_fk_sql(table)
    return migrations.RunSQL(forward, reverse)


class Migration(migrations.Migration):
    dependencies = [("pos", "0001_initial"), ("iam", "0002_security"), ("kernel", "0001_initial")]

    operations = [
        *[rls.enable(t) for t in TABLES],
        *[_company_fk(t) for t in TABLES],
        branch_fk("pos_config"),
        *[immutable_trigger(t) for t in POSTED],
    ]
