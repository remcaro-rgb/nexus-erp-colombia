"""T-PORTAL-EMP-01 (integration, G2): an employee sees only own payslips/certificates; the certificate PDF is
labelled with the fixture parameter status."""

from __future__ import annotations

import pytest

from kernel import clock

from .conftest import add_payslip

pytestmark = pytest.mark.django_db


def test_T_PORTAL_EMP_01_employee_sees_only_own_payslips(employees_world, payslips, renderer):
    w = employees_world
    a1 = add_payslip(payslips, w["a"], "2026-09", "2300000.0000")
    a2 = add_payslip(payslips, w["a"], "2026-10", "2350000.0000")
    b1 = add_payslip(payslips, w["b"], "2026-10", "9100000.0000")
    c1 = add_payslip(payslips, w["c"], "2026-10", "1800000.0000")

    r = w["api_a"].get("/api/portal/v1/employee/payslips")
    assert r.status_code == 200, r.content
    items = r.json()["items"]
    assert [i["id"] for i in items] == [str(a2.id), str(a1.id)]  # newest first, only own
    assert items[0] == {
        "id": str(a2.id),
        "tenant_id": str(a2.tenant_id),
        "status": "POSTED",
        "period": "2026-10",
        "net_pay": {"amount": "2350000.0000", "currency": "COP"},
        "version": 1,
        "audit": {"channel": "portal"},
    }
    body = r.content.decode()
    assert str(b1.id) not in body and "9100000" not in body and str(c1.id) not in body

    detail = w["api_a"].get(f"/api/portal/v1/employee/payslips/{a1.id}")
    assert detail.status_code == 200 and detail.json()["net_pay"]["amount"] == "2300000.0000"
    pdf = w["api_a"].get(f"/api/portal/v1/employee/payslips/{a1.id}/pdf")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    template, data, mark = renderer.calls[-1]
    assert template == "hr.desprendible" and data["employee"] == w["a"].full_name and data["net_pay"] == "2300000.0000"
    assert "NO LEGALMENTE VERIFICADO" in mark and "smmlv=VERIFIED-PRIMARY (FIXTURE — NOT LEGALLY VERIFIED)" in mark

    # employee B sees only B's payslip
    items_b = w["api_b"].get("/api/portal/v1/employee/payslips").json()["items"]
    assert [i["id"] for i in items_b] == [str(b1.id)]


def test_T_PORTAL_EMP_01_certificates_own_and_labelled_with_parameter_status(employees_world, payslips, renderer):
    w = employees_world
    add_payslip(payslips, w["a"], "2026-09", "2300000.0000")
    add_payslip(payslips, w["a"], "2026-10", "2350000.0000")
    add_payslip(payslips, w["b"], "2026-10", "9100000.0000")
    year = clock.business_date().year

    r = w["api_a"].get("/api/portal/v1/employee/certificates")
    assert r.status_code == 200, r.content
    certs = r.json()["items"]
    assert sorted((c["certificate_type"], c["year"]) for c in certs) == [
        ("INGRESOS_RETENCIONES", 2026),
        ("LABORAL", year),
    ]
    assert all(set(c) == {"id", "tenant_id", "status", "certificate_type", "year", "version", "audit"} for c in certs)
    certs_b = {c["id"] for c in w["api_b"].get("/api/portal/v1/employee/certificates").json()["items"]}
    assert certs_b.isdisjoint({c["id"] for c in certs})  # ids are per employee

    by_type = {c["certificate_type"]: c for c in certs}
    income = by_type["INGRESOS_RETENCIONES"]
    assert w["api_a"].get(f"/api/portal/v1/employee/certificates/{income['id']}").json() == income
    pdf = w["api_a"].get(f"/api/portal/v1/employee/certificates/{income['id']}/pdf")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    template, data, mark = renderer.calls[-1]
    assert template == "hr.certificado_ingresos_retenciones"
    # only A's own payslips are totalled
    assert data["payslips"] == 2 and data["total_net"] == "4650000.0000" and data["employee"] == w["a"].full_name
    # labelled: synthetic watermark + every parameter with its status and the fixture flag
    assert mark.startswith("SINTÉTICO — NO LEGALMENTE VERIFICADO")
    assert "smmlv=VERIFIED-PRIMARY (FIXTURE — NOT LEGALLY VERIFIED)" in mark
    assert "ss.salud.empleado=VERIFIED-PRIMARY (FIXTURE — NOT LEGALLY VERIFIED)" in mark
    assert "uvt=VERIFIED-PRIMARY (FIXTURE — NOT LEGALLY VERIFIED)" in mark
    assert data["parameter_status"] in mark and b"NO LEGALMENTE VERIFICADO" in pdf.content

    laboral = w["api_a"].get(f"/api/portal/v1/employee/certificates/{by_type['LABORAL']['id']}/pdf")
    assert laboral.status_code == 200
    template, data, mark = renderer.calls[-1]
    assert template == "hr.certificado_laboral" and data["salary"] == "2500000.0000"
    assert data["contract_type"] == "INDEFINIDO" and "smmlv=VERIFIED-PRIMARY (FIXTURE" in mark


def test_T_PORTAL_EMP_01_unknown_parameter_is_labelled_unknown_not_invented(
    employees_world, payslips, renderer, parameters
):
    w = employees_world
    add_payslip(payslips, w["a"], "2026-10", "2350000.0000")
    parameters.values.pop("uvt")
    income = next(
        c for c in w["api_a"].get("/api/portal/v1/employee/certificates").json()["items"]
        if c["certificate_type"] == "INGRESOS_RETENCIONES"
    )  # fmt: skip
    assert w["api_a"].get(f"/api/portal/v1/employee/certificates/{income['id']}/pdf").status_code == 200
    assert "uvt=UNKNOWN" in renderer.calls[-1][2]


def test_T_PORTAL_EMP_01_profile_is_own(employees_world):
    w = employees_world
    r = w["api_a"].get("/api/portal/v1/employee/profile")
    assert r.status_code == 200
    assert r.json()["full_name"] == w["a"].full_name and r.json()["id"] == str(w["a"].id)
    assert w["b"].full_name not in r.content.decode()


def test_T_PORTAL_EMP_01_no_payslip_provider_fails_closed(employees_world):
    from hr.interfaces import EmployeePayslipSource
    from kernel import services

    previous = services.optional(EmployeePayslipSource)
    services.unregister(EmployeePayslipSource)
    try:
        r = employees_world["api_a"].get("/api/portal/v1/employee/payslips")
        assert r.status_code == 503 and r.json()["code"] == "service_unavailable"
        kpis = employees_world["api_a"].get("/api/portal/v1/employee/kpis").json()["kpis"]
        assert {k["id"]: k["state"] for k in kpis}["ep_ultimo_pago"] == "not_available"
    finally:
        if previous is not None:
            services.register(EmployeePayslipSource, previous, replace=True)
