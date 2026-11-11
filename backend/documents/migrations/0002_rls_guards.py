"""Row-level security and append-only guards for the documents tables (ADR-003, C1-5).

* ENABLE + FORCE RLS with the tenant + company predicate on every table (unbound sessions see no rows).
* Template versions and generated documents are append-only (``kernel_forbid_mutation`` without a posted column).
"""

from django.db import migrations

from iam import rls
from kernel.db import immutable_trigger

TABLES = ["documents_template", "documents_template_version", "documents_generated", "documents_render_batch"]


class Migration(migrations.Migration):
    dependencies = [("documents", "0001_initial"), ("iam", "0002_security"), ("kernel", "0001_initial")]

    operations = [
        *[rls.enable(t) for t in TABLES],
        immutable_trigger("documents_template_version", None),
        immutable_trigger("documents_generated", None),
    ]
