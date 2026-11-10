"""Row-level security for every payroll table and the immutability of posted payslips (ADR-003, C1-5).

ENABLE + FORCE RLS (tenant + bound companies); unbound sessions see no rows. Payslips and their lines
become immutable once ``posted`` is true (DB trigger): a posted payroll is never re-evaluated.
"""

from django.db import migrations

from iam import rls
from kernel.db import immutable_trigger

TABLES = [
    "payroll_period",
    "payroll_concept",
    "payroll_novelty",
    "payroll_run",
    "payroll_payslip",
    "payroll_payslip_line",
    "payroll_electronic_document",
]


class Migration(migrations.Migration):
    dependencies = [("payroll", "0001_initial"), ("iam", "0002_security")]

    operations = [
        *[rls.enable(t) for t in TABLES],
        immutable_trigger("payroll_payslip"),
        immutable_trigger("payroll_payslip_line"),
    ]
