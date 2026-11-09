"""T-P07-10 DSR SLA business-day computation with holidays (synthetic calendar, NOT LEGALLY VERIFIED)."""

from __future__ import annotations

import datetime as dt

import pytest

from kernel.errors import Conflict, ParameterUnknown
from workflow.dsr.calendar import (
    FIXTURE_LABEL,
    FixtureHolidayCalendar,
    add_business_days,
    business_days_between,
    calendar,
)
from workflow.dsr.registry import DSR_SOURCES, PersonalDataSource, register_source
from workflow.dsr.service import DsrService, sla

D = dt.date
# Synthetic holidays for the test only — not an official calendar (P06 owns the real one).
CAL = FixtureHolidayCalendar([D(2026, 11, 16), D(2026, 12, 8), D(2026, 12, 25)])


def test_T_P07_10_fixture_is_labelled_and_missing_calendar_fails_closed() -> None:
    assert CAL.label == FIXTURE_LABEL
    with pytest.raises(ParameterUnknown):
        calendar()


@pytest.mark.parametrize(
    ("start", "days", "expected"),
    [
        (D(2026, 11, 9), 1, D(2026, 11, 10)),
        (D(2026, 11, 13), 1, D(2026, 11, 17)),  # Fri → skips weekend and the Monday holiday
        (D(2026, 11, 14), 1, D(2026, 11, 17)),  # received on Saturday
        (D(2026, 11, 9), 10, D(2026, 11, 24)),
        (D(2026, 12, 1), 15, D(2026, 12, 23)),
        (D(2026, 12, 18), 5, D(2026, 12, 28)),
        (D(2026, 11, 9), 0, D(2026, 11, 9)),
    ],
)
def test_T_P07_10_business_days(start, days, expected) -> None:  # type: ignore[no-untyped-def]
    assert add_business_days(start, days, CAL) == expected
    assert business_days_between(start, expected, CAL) == days


def test_T_P07_10_sla_consulta_and_reclamo() -> None:
    assert sla("consulta", D(2026, 11, 9), CAL) == (D(2026, 11, 24), D(2026, 12, 1))  # 10 + 5
    assert sla("reclamo", D(2026, 11, 9), CAL) == (D(2026, 12, 1), D(2026, 12, 14))  # 15 + 8 (8 Dec holiday)


@pytest.mark.django_db
def test_T_P07_10_dsr_workflow(user_factory, company) -> None:  # type: ignore[no-untyped-def]
    perms = ["workflow.dsr_request.create", "workflow.dsr_request.update", "workflow.dsr_request.read",
             "workflow.dsr_request.post", "workflow.dsr_request.approve"]  # fmt: skip
    ctx = user_factory(*perms)
    calls: list[str] = []
    register_source(
        PersonalDataSource(
            "test.customer",
            frozenset({"customer"}),
            ("name", "email"),
            find=lambda c, ref: [{"name": "Ana Sintética", "ref": ref}],
        ),
        replace=True,
    )
    register_source(
        PersonalDataSource(
            "test.invoice",
            frozenset({"customer"}),
            ("buyer",),
            find=lambda c, ref: [{"buyer": ref}],
            suppress=lambda c, ref: calls.append(ref) or 1,
            retention="Ley 962/2005 art. 28: conservación 10 años",
        ),
        replace=True,
    )
    try:
        svc = DsrService(CAL)
        req = svc.intake(
            ctx,
            company,
            kind="consulta",
            right="access",
            subject_type="customer",
            subject_ref="C-1",
            requester_name="Ana Sintética",
            received_on=D(2026, 11, 9),
        )
        assert req.due_on == D(2026, 11, 24) and req.max_due_on == D(2026, 12, 1)
        with pytest.raises(Conflict):
            svc.discover(ctx, req.id)  # identity first
        svc.verify_identity(ctx, req.id, "documento + código enviado al correo")
        assert set(svc.discover(ctx, req.id)) == {"test.customer", "test.invoice"}
        extended = svc.extend(ctx, req.id, "volumen de información")
        assert extended.due_on == D(2026, 12, 1)
        with pytest.raises(Conflict):
            svc.extend(ctx, req.id, "otra vez")
        done = svc.fulfil(ctx, req.id, "Se entrega el paquete")
        assert done.state == "CLOSED" and done.export["package"]["test.customer"]["records"][0]["ref"] == "C-1"

        sup = svc.intake(
            ctx,
            company,
            kind="reclamo",
            right="suppression",
            subject_type="customer",
            subject_ref="C-2",
            requester_name="Luis Sintético",
            received_on=D(2026, 11, 9),
        )
        svc.verify_identity(ctx, sup.id, "presencial")
        out = svc.fulfil(ctx, sup.id)
        assert "Ley 962/2005" in out.denial_reason and calls == []  # retention explains the denial
        assert svc.overdue(ctx, D(2026, 12, 31)) == []
    finally:
        DSR_SOURCES.pop("test.customer", None)
        DSR_SOURCES.pop("test.invoice", None)


@pytest.mark.django_db
def test_T_P07_10_requester_pii_is_hashed_in_audit(user_factory, company) -> None:  # type: ignore[no-untyped-def]
    from workflow.models import AuditEvent

    ctx = user_factory("workflow.dsr_request.create")
    DsrService(CAL).intake(
        ctx,
        company,
        kind="consulta",
        right="access",
        subject_type="employee",
        subject_ref="E-1",
        requester_name="Nombre Sintético",
        received_on=D(2026, 11, 9),
    )
    ev = AuditEvent.objects.get(tenant_id=ctx.tenant_id, action="workflow.dsr_request.create")
    assert "Nombre" not in str(ev.changes) and ev.changes["requester_name"]["after"].startswith("sha256:")
