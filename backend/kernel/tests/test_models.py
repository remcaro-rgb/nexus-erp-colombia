import uuid

import pytest
from django.db import DatabaseError, connection, models, transaction

from kernel import db
from kernel.errors import ConcurrencyConflict, ImmutableRecord, PreconditionFailed, PreconditionRequired
from kernel.models import AuditedModel, CompanyModel, ImmutableModel


class LedgerProbe(CompanyModel, AuditedModel, ImmutableModel):
    memo = models.CharField(max_length=50, default="")

    class Meta:
        app_label = "kernel"
        db_table = "kernel_test_ledger_probe"
        managed = False


class AppendOnlyProbe(CompanyModel, ImmutableModel):
    IMMUTABLE_ALWAYS = True

    class Meta:
        app_label = "kernel"
        db_table = "kernel_test_append_only_probe"
        managed = False


@pytest.fixture
def probe_tables(django_db_blocker):
    fwd, rev = db.immutable_trigger_sql("kernel_test_ledger_probe", "posted")
    fwd2, rev2 = db.immutable_trigger_sql("kernel_test_append_only_probe", None)
    with django_db_blocker.unblock():
        with connection.schema_editor() as editor:
            editor.create_model(LedgerProbe)
            editor.create_model(AppendOnlyProbe)
        with connection.cursor() as cur:
            cur.execute(fwd)
            cur.execute(fwd2)
    yield
    with django_db_blocker.unblock():
        with connection.cursor() as cur:
            cur.execute(rev)
            cur.execute(rev2)
        with connection.schema_editor() as editor:
            editor.delete_model(LedgerProbe)
            editor.delete_model(AppendOnlyProbe)


def _probe(**kw):
    return LedgerProbe.objects.create(tenant_id=uuid.uuid4(), company_id=uuid.uuid4(), **kw)


@pytest.mark.django_db(transaction=True)
def test_immutable_rows_app_guard_and_db_trigger(probe_tables):
    draft = _probe()
    draft.memo = "edit allowed while draft"
    draft.save()
    draft.posted = True
    draft.save()  # posting itself is allowed
    with pytest.raises(ImmutableRecord):
        draft.save()
    with pytest.raises(ImmutableRecord):
        draft.delete()
    # DB trigger: bypassing the ORM guard still fails
    with pytest.raises(DatabaseError, match="immutable row"), transaction.atomic():
        LedgerProbe.objects.filter(pk=draft.pk).update(memo="sneaky")
    with pytest.raises(DatabaseError, match="immutable row"), transaction.atomic():
        LedgerProbe.objects.filter(pk=draft.pk).delete()
    other = _probe()
    other.delete()  # drafts can be deleted

    row = AppendOnlyProbe.objects.create(tenant_id=uuid.uuid4(), company_id=uuid.uuid4())
    with pytest.raises(ImmutableRecord):
        row.save()
    with pytest.raises(DatabaseError, match="immutable row"), transaction.atomic():
        AppendOnlyProbe.objects.filter(pk=row.pk).update(posted=True)


@pytest.mark.django_db(transaction=True)
def test_optimistic_locking_and_etag(probe_tables):
    row = _probe()
    stale = LedgerProbe.objects.get(pk=row.pk)
    etag = row.etag
    row.check_if_match(etag)
    row.check_if_match("*")
    with pytest.raises(PreconditionRequired):
        row.check_if_match(None)
    row.memo = "v2"
    actor = uuid.uuid4()
    row.save_versioned(actor, ["memo"])
    assert row.version == 2 and row.updated_by == actor and row.etag != etag
    with pytest.raises(PreconditionFailed):
        row.check_if_match(etag)
    stale.memo = "lost update"
    with pytest.raises(ConcurrencyConflict):
        stale.save_versioned(actor, ["memo"])
    assert LedgerProbe.objects.get(pk=row.pk).memo == "v2"


def test_sql_helpers_validate_identifiers():
    with pytest.raises(ValueError):
        db.immutable_trigger_sql("x; drop table y")
    fwd, rev = db.company_fk_sql("sales_invoice")
    assert "FOREIGN KEY (tenant_id, company_id) REFERENCES iam_company (tenant_id, id)" in fwd
    assert "DROP CONSTRAINT IF EXISTS sales_invoice_company_fk" in rev
    fwd, _ = db.rls_policy_sql("iam_branch", company_scoped=False)
    assert "FORCE ROW LEVEL SECURITY" in fwd and "company_ids" not in fwd
    op = db.immutable_trigger("ledger_journal_line", None)
    assert "kernel_forbid_mutation('')" in op.sql
