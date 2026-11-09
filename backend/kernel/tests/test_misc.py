import base64
import datetime as dt
import os
import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured

from kernel import cache, clock, dates, dimensions, events, flags, integrations, parameters, services
from kernel.errors import ParameterNotVerified, ParameterUnknown, ServiceUnavailable, ValidationFailed
from kernel.interfaces import PostingService
from kernel.models import FeatureFlagOverride
from kernel.parameters import ParameterStatus, ParameterValue
from kernel.testing.fakes import FakePostingService
from kernel.testing.tenancy import bound, first_company, make_context


def test_clock_frozen_and_business_date():
    at = dt.datetime(2026, 11, 10, 3, 30, tzinfo=dt.UTC)  # 22:30 on the 9th in Bogotá
    with clock.frozen(at) as fc:
        assert clock.now() == at
        assert clock.business_date() == dt.date(2026, 11, 9)
        fc.tick(dt.timedelta(hours=2))
        assert clock.business_date() == dt.date(2026, 11, 10)
    assert isinstance(clock.get_clock(), clock.SystemClock)
    assert clock.local_midnight_utc(dt.date(2026, 11, 9)) == dt.datetime(2026, 11, 9, 5, tzinfo=dt.UTC)
    with pytest.raises(ValueError):
        clock.FrozenClock(dt.datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        clock.business_date(at=dt.datetime(2026, 1, 1))


def test_dimension_slot_registry():
    keys = [s.key for s in dimensions.slots()]
    assert keys == ["aux_abierto", "business_unit", "cost_center", "item_contable"]
    assert dimensions.line_slots() == ["aux_abierto_id", "business_unit_id", "cost_center_id", "item_dimension_id"]
    assert dimensions.get("item_contable").line_slot == "item_dimension_id"
    assert dimensions.by_line_slot("cost_center_id").company_scoped
    assert all(s.company_scoped for s in dimensions.slots())
    with pytest.raises(KeyError):
        dimensions.get("project")
    with pytest.raises(KeyError):
        dimensions.by_line_slot("nope_id")
    cc = dimensions.get("cost_center")
    assert dimensions.register(cc) is cc  # idempotent
    with pytest.raises(ValueError):
        dimensions.register(dimensions.DimensionSlot("dup", "cost_center_id", "x.Y", True, "Dup", "/x"))
    with pytest.raises(ValueError):
        dimensions.register(dimensions.DimensionSlot("cost_center", "cc2_id", "x.Y", True, "CC", "/x"))
    assert dimensions.validate_depth(dimensions.DEFAULT_MAX_DEPTH) == 6
    for bad in (0, 11):
        with pytest.raises(ValueError):
            dimensions.validate_depth(bad)


def test_date_range_helper():
    d = dt.date
    r = dates.DateRange(d(2026, 1, 1), d(2026, 6, 30))
    assert r.contains(d(2026, 1, 1)) and r.contains(d(2026, 6, 30)) and not r.contains(d(2026, 7, 1))
    assert not r.contains(d(2025, 12, 31))
    open_end = dates.DateRange(d(2026, 6, 1), None)
    assert r.overlaps(open_end) and open_end.overlaps(r)
    assert r.intersection(open_end) == dates.DateRange(d(2026, 6, 1), d(2026, 6, 30))
    assert r.intersection(dates.DateRange(d(2027, 1, 1))) is None
    assert dates.DateRange().intersection(dates.DateRange()) == dates.DateRange()
    assert not r.overlaps(dates.DateRange(None, d(2025, 12, 31)))
    with pytest.raises(ValueError):
        dates.DateRange(d(2026, 2, 1), d(2026, 1, 1))
    assert dates.is_effective(None, None, d(2000, 1, 1))
    q = dates.effective_q(d(2026, 3, 1))
    assert "valid_from__lte" in str(q) and "valid_to__gte" in str(q)
    q2 = dates.overlapping_q(r, "start", "end")
    assert "start__lte" in str(q2) and "end__gte" in str(q2)
    assert str(dates.overlapping_q(dates.DateRange())) == str(__import__("django.db.models", fromlist=["Q"]).Q())


@pytest.mark.django_db
def test_date_helper_filters_rows(ctx, company, make_sequence):
    from kernel.models import Sequence

    make_sequence(sequence_code="OLD", valid_from=dt.date(2025, 1, 1), valid_to=dt.date(2025, 12, 31))
    make_sequence(sequence_code="CUR", valid_from=dt.date(2026, 1, 1))
    make_sequence(sequence_code="ANY")
    on = dt.date(2026, 5, 1)
    rng = dates.DateRange(dt.date(2025, 12, 1), dt.date(2026, 1, 15))
    with bound(ctx):
        assert sorted(Sequence.objects.filter(dates.effective_q(on)).values_list("sequence_code", flat=True)) == [
            "ANY",
            "CUR",
        ]
        assert Sequence.objects.filter(dates.overlapping_q(rng)).count() == 3


def test_cache_keys_isolate_company_role_tenant_and_invalidate():
    ctx = make_context(company_ids=[uuid.uuid4(), uuid.uuid4()])
    c1, c2 = sorted(ctx.company_ids)
    admin, clerk = cache.permission_hash(["admin"], 3), cache.permission_hash(["clerk"], 3)
    assert cache.permission_hash(["a", "b"], 1) == cache.permission_hash(["b", "a", "a"], 1)
    assert cache.permission_hash(["a"], 1) != cache.permission_hash(["a"], 2)
    k = cache.ui_cache_key("menu", ctx, company_id=c1, period_id="P1", perm_hash=admin)
    assert k != cache.ui_cache_key("menu", ctx, company_id=c2, period_id="P1", perm_hash=admin)
    assert k != cache.ui_cache_key("menu", ctx, company_id=c1, period_id="P1", perm_hash=clerk)
    assert k != cache.ui_cache_key("menu", ctx, company_id=c1, period_id="P2", perm_hash=admin)
    other_tenant = make_context(company_ids=[c1], principal_id=ctx.principal_id)
    assert k != cache.ui_cache_key("menu", other_tenant, company_id=c1, period_id="P1", perm_hash=admin)
    with pytest.raises(ValidationFailed):
        cache.ui_cache_key("menu", ctx, company_id=uuid.uuid4(), period_id=None, perm_hash=admin)
    assert cache.generation(ctx) == 0
    assert cache.on_context_switch(ctx) == 1
    assert k != cache.ui_cache_key("menu", ctx, company_id=c1, period_id="P1", perm_hash=admin)
    assert cache.on_logout(ctx) == 2


@pytest.mark.django_db
def test_feature_flags_resolution(ctx, company):
    code = flags.POS_OFFLINE_FISCAL_SELLING
    assert flags.definition(code).kind == "A" and not flags.is_enabled(code, ctx.tenant_id, company)
    with bound(ctx):  # kernel_feature_flag has forced RLS (kernel migration 0002)
        FeatureFlagOverride.objects.create(tenant_id=ctx.tenant_id, company_id=None, code=code, enabled=True)
        assert flags.is_enabled(code, ctx.tenant_id, company)
        assert flags.is_enabled(code, ctx.tenant_id)
        FeatureFlagOverride.objects.create(tenant_id=ctx.tenant_id, company_id=company, code=code, enabled=False)
        assert not flags.is_enabled(code, ctx.tenant_id, company)
        assert not flags.is_enabled(code, uuid.uuid4(), company)
    with pytest.raises(ValueError):
        flags.register("bad_a_flag", default=True, kind="A")
    flags.register("core_thing", default=True, kind="P")
    assert flags.register("core_thing", default=True, kind="P").default
    with pytest.raises(ValueError):
        flags.register("core_thing", default=False, kind="P")
    with pytest.raises(KeyError):
        flags.definition("nope")
    assert code in flags.registered()


def test_parameters_fail_closed():
    on = dt.date(2026, 1, 1)
    with pytest.raises(ParameterUnknown):
        parameters.enforce(None, "UVT", production=False)
    unknown = ParameterValue("UVT", None, ParameterStatus.UNKNOWN, "S-00")
    with pytest.raises(ParameterUnknown):
        parameters.enforce(unknown, "UVT", production=False)
    verified = ParameterValue("UVT", Decimal(1), ParameterStatus.VERIFIED_PRIMARY, "S-01", on, None)
    assert parameters.enforce(verified, "UVT", production=False) is verified
    with pytest.raises(ParameterNotVerified):
        parameters.enforce(verified, "UVT", production=True)
    stamped = ParameterValue("UVT", Decimal(1), ParameterStatus.DISPUTED, "S-01", legal_review_stamp="LR-1")
    assert parameters.enforce(stamped, "UVT", production=True).requires_warning
    snap = stamped.snapshot()
    assert snap["status"] == "DISPUTED" and snap["value"] == "1" and snap["valid_from"] is None
    assert verified.snapshot()["valid_from"] == "2026-01-01"


def test_integration_modes_refuse_production():
    assert integrations.validate_integration_modes({"dian": " sandbox "}) == {"dian": "SANDBOX"}
    with pytest.raises(ImproperlyConfigured):
        integrations.validate_integration_modes({"dian": "PRODUCTION"})


def test_settings_refuse_unsupported_environment(monkeypatch):
    import importlib
    import sys

    monkeypatch.setenv("ERP_ENVIRONMENT", "prod")
    sys.modules.pop("erp.settings.base", None)
    try:
        with pytest.raises(ImproperlyConfigured, match="DEV-only"):
            importlib.import_module("erp.settings.base")
        monkeypatch.setenv("ERP_ENVIRONMENT", "dev")
        monkeypatch.setenv("ERP_PUBLIC_ORIGIN", "ftp://nope")
        sys.modules.pop("erp.settings.base", None)
        with pytest.raises(ImproperlyConfigured, match="ERP_PUBLIC_ORIGIN"):
            importlib.import_module("erp.settings.base")
        monkeypatch.setenv("ERP_PUBLIC_ORIGIN", "https://erp.example.test")
        sys.modules.pop("erp.settings.base", None)
        base = importlib.import_module("erp.settings.base")
        assert base.ALLOWED_HOSTS == ["127.0.0.1", "erp.example.test", "localhost"]
        assert base.CSRF_TRUSTED_ORIGINS == ["https://erp.example.test"]
        assert base.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
        assert base.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == ["rest_framework.permissions.IsAuthenticated"]
        sys.modules.pop("erp.settings.dev", None)
        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
        # the dev profile requires the field-encryption key (FIX-D-W01-1); an ephemeral one for this import
        monkeypatch.setenv("ERP_FIELD_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
        dev = importlib.import_module("erp.settings.dev")
        assert len(dev.SECRET_KEY) >= 50 and dev.ERP_ENVIRONMENT == "dev"
    finally:
        monkeypatch.undo()
        sys.modules.pop("erp.settings.base", None)
        sys.modules.pop("erp.settings.dev", None)


def test_service_registry():
    fake = FakePostingService()
    with pytest.raises(ServiceUnavailable):
        services.get(PostingService)
    assert services.optional(PostingService) is None
    with services.override(PostingService, fake):
        assert services.get(PostingService) is fake
        other = FakePostingService()
        with services.override(PostingService, other):
            assert services.get(PostingService) is other
        assert services.get(PostingService) is fake
    assert services.optional(PostingService) is None
    with pytest.raises(TypeError):
        services.register(PostingService, object())
    services.register(PostingService, fake)
    try:
        with pytest.raises(ValueError):
            services.register(PostingService, fake)
    finally:
        services.unregister(PostingService)


def test_event_envelope_rules():
    ctx = make_context()
    with pytest.raises(ValidationFailed):
        events.validate_event_type("Sales.Invoice.posted")
    with pytest.raises(ValidationFailed):
        events.DomainEvent.build(ctx, "sales.invoice.posted.v1", {"x": 0.5})  # a float payload is rejected
    with pytest.raises(ValidationFailed):
        events.DomainEvent.build(ctx, "sales.invoice.posted.v1", {}, company_id=uuid.uuid4())
    from kernel.money import Money

    e = events.DomainEvent.build(
        ctx,
        "sales.invoice.posted.v3",
        {"total": Money.of("10.50"), "on": dt.date(2026, 1, 2), "tags": {"b", "a"}, "nested": [{"q": Decimal("1")}]},
        company_id=first_company(ctx),
    )
    env = e.envelope()
    assert env["schema_version"] == 3 and e.module == "sales"
    assert env["payload"] == {
        "total": {"amount": "10.50", "currency": "COP"},
        "on": "2026-01-02",
        "tags": ["a", "b"],
        "nested": [{"q": "1"}],
    }
    with pytest.raises(TypeError):
        events.to_jsonable({"x": object()})
