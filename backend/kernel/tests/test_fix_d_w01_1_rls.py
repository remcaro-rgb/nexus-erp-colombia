"""FIX-D-W01-1: forced RLS on the kernel's tenant tables (kernel migration 0002, ADR-003).

Backs the DR-drill invariant ``rls_forced_on_tenant_tables`` (T-P03-03) with behaviour tests:
tenant isolation, unbound sessions see nothing, and the ``outbox`` platform scope reaches only
kernel_outbox / kernel_dead_letter.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import DatabaseError, connection, transaction

from kernel import tenancy, uow
from kernel.errors import TenancyNotBound, ValidationFailed
from kernel.models import DeadLetter, FeatureFlagOverride, IdempotencyRecord, OutboxMessage, Sequence
from kernel.testing.tenancy import bound, first_company, make_context, platform

KERNEL_TENANT_MODELS = [Sequence, IdempotencyRecord, FeatureFlagOverride, OutboxMessage, DeadLetter]


def _emit(ctx):
    with uow.unit_of_work(ctx) as u:
        u.emit("kernel.test.created.v1", {"n": 1}, company_id=first_company(ctx))


def _forced(table):
    with connection.cursor() as cur:
        cur.execute(tenancy.RLS_STATUS_QUERY, [table])
        return cur.fetchone()


@pytest.mark.django_db
def test_FIX_D_W01_1_every_kernel_tenant_table_has_forced_rls_and_a_policy():
    for model in KERNEL_TENANT_MODELS:
        assert _forced(model._meta.db_table) == (True, True, True), model._meta.db_table


@pytest.mark.django_db(transaction=True)
def test_FIX_D_W01_1_kernel_tables_isolate_tenants_and_unbound_sees_nothing():
    a, b = make_context(), make_context()
    for ctx in (a, b):
        _emit(ctx)
        with bound(ctx):
            Sequence.objects.create(tenant_id=ctx.tenant_id, company_id=first_company(ctx), sequence_code="ISO")
    with transaction.atomic():  # unbound: deny by default
        assert OutboxMessage.objects.count() == 0
        assert Sequence.objects.count() == 0
    for ctx in (a, b):
        with bound(ctx):
            assert set(OutboxMessage.objects.values_list("tenant_id", flat=True)) == {ctx.tenant_id}
            assert set(Sequence.objects.values_list("tenant_id", flat=True)) == {ctx.tenant_id}
    other_company = make_context(tenant_id=a.tenant_id)  # same tenant, a company it may not see
    with bound(other_company):
        assert Sequence.objects.count() == 0  # kernel_sequence is company-scoped


@pytest.mark.django_db(transaction=True)
def test_FIX_D_W01_1_writes_for_another_tenant_or_unbound_are_rejected():
    a = make_context()
    with pytest.raises(DatabaseError, match="row-level security"), transaction.atomic():
        Sequence.objects.create(tenant_id=a.tenant_id, company_id=first_company(a), sequence_code="NOPE")
    with pytest.raises(DatabaseError, match="row-level security"), bound(a):
        FeatureFlagOverride.objects.create(tenant_id=uuid.uuid4(), company_id=None, code="x", enabled=True)


@pytest.mark.django_db(transaction=True)
def test_FIX_D_W01_1_outbox_platform_scope_spans_tenants_only_on_outbox_tables():
    a, b = make_context(), make_context()
    for ctx in (a, b):
        _emit(ctx)
        with bound(ctx):
            Sequence.objects.create(tenant_id=ctx.tenant_id, company_id=first_company(ctx), sequence_code="P")
    with platform("outbox"):
        assert set(OutboxMessage.objects.values_list("tenant_id", flat=True)) == {a.tenant_id, b.tenant_id}
        assert Sequence.objects.count() == 0  # the platform scope is not a tenant binding
        assert IdempotencyRecord.objects.count() == 0
    with pytest.raises(ValidationFailed):
        with transaction.atomic():
            tenancy.bind_platform("everything")
    with pytest.raises(TenancyNotBound):
        tenancy.bind_platform("outbox")
