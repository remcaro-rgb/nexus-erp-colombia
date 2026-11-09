"""Tax calendar, reminders, management commands, demo-seed loader and exógena import."""

from __future__ import annotations

import datetime as dt
import io
import json
import uuid
from typing import Any

import pytest
from django.core.management import call_command

from kernel.testing.fakes import FakeSchedulerService
from tax.calendar import due_date, schedule_reminders, upcoming, validate_due_dates
from tax.exogena.loader import synthetic_pack_data
from tax.models import Parameter, TaxCalendar, WithholdingConcept
from tax.testing.fixtures import ApiClient, grant_all

pytestmark = pytest.mark.django_db

# Synthetic calendar data (the yearly decree is a tenant import, R-TX-07).
DUE = {"periods": [{"period": "2026-10", "by_digit": {"1": "2026-11-10", "2": "2026-11-11", "01": "2026-11-09"}}]}


def test_P06_calendar_due_dates_by_nit_digit(seeded: Any, company: uuid.UUID) -> None:
    validate_due_dates(DUE, 2026)
    with pytest.raises(Exception):
        validate_due_dates({"periods": [{"period": "2026-10", "by_digit": {"x": "2026-01-01"}}]})
    cal = TaxCalendar.objects.create(
        tenant_id=seeded.tenant_id,
        company_id=company,
        code="RF",
        name="Retención",
        obligation="retefuente",
        year=2026,
        due_dates=DUE,
        synthetic=True,
    )
    assert due_date(cal, "2026-10", "800197201") == dt.date(2026, 11, 9)  # two-digit key wins
    assert due_date(cal, "2026-10", "800197262") == dt.date(2026, 11, 11)
    with pytest.raises(Exception) as exc:
        due_date(cal, "2026-09", "800197262")
    assert exc.value.code == "not_found"
    assert upcoming(cal, "800197262", dt.date(2026, 11, 1)) == [
        {"obligation": "retefuente", "period": "2026-10", "due_date": "2026-11-11"}
    ]
    scheduler = FakeSchedulerService()
    job = schedule_reminders(scheduler, seeded, company, cal)
    assert job.task_name == "tax.calendar.remind" and job.args["calendar_id"] == str(cal.id)


def test_P06_load_tax_parameters_command_is_idempotent(db: Any) -> None:
    tenant, principal, company = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    args = ["--tenant", str(tenant), "--principal", str(principal), "--company", str(company), "--synthetic-dev"]
    out = io.StringIO()
    call_command("load_tax_parameters", *args, stdout=out)
    first = Parameter.objects.filter(tenant_id=tenant).count()
    call_command("load_tax_parameters", *args, stdout=io.StringIO())
    assert Parameter.objects.filter(tenant_id=tenant).count() == first > 30
    assert WithholdingConcept.objects.filter(tenant_id=tenant, company_id=company, code="SYN-RF-SERVICIOS").exists()
    assert Parameter.objects.filter(tenant_id=tenant, code__startswith="syn.", synthetic=True).count() == 5
    assert "tax:" in out.getvalue()


def test_P06_localization_commands(db: Any, tmp_path: Any) -> None:
    csv = tmp_path / "divipola.csv"
    csv.write_text(
        "departamento_codigo,departamento,municipio_codigo,municipio\n05,Antioquia,5002,Abejorral\n", encoding="utf-8"
    )
    out = io.StringIO()
    call_command("import_divipola", "--file", str(csv), stdout=out)
    call_command("import_divipola", stdout=out)
    call_command("load_code_lists", stdout=out)
    from localization_co.models import Municipality

    assert Municipality.objects.get(code="05002").name == "Abejorral"
    assert "divipola: 10 departments" in out.getvalue()


def test_P06_demo_seed_loaders_registered(db: Any, tmp_path: Any) -> None:
    from kernel import seed

    tenant, company = uuid.uuid4(), uuid.uuid4()
    path = tmp_path / "demo.yaml"
    path.write_text(
        json.dumps(
            {
                "synthetic": True,
                "tenant_id": str(tenant),
                "seed_principal_id": str(uuid.uuid4()),
                "company_ids": [str(company)],
                "localization": True,
                "tax": {"synthetic_dev": True},
            }
        ),
        encoding="utf-8",
    )
    counts = seed.load(path)
    assert counts["tax"] > 0 and counts["localization"] > 0
    assert Parameter.objects.filter(tenant_id=tenant, code="uvt").exists()


def test_P06_exogena_import_endpoint(seeded: Any, api: ApiClient, policy: Any) -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    grant_all(policy, seeded)
    pack = synthetic_pack_data()
    pack.update(pack_code="IMP-TEST-1001", source="OFFICIAL_IMPORTED", legal_status="PENDING_VERIFICATION")
    upload = SimpleUploadedFile("pack.json", json.dumps(pack).encode(), content_type="application/json")
    resp = api.client.post(
        "/api/v1/tax/exogena/format-packs:import",
        {"file": upload},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="import-key-0001",
    )
    assert resp.status_code == 202, resp.json()
    result = resp.json()["result"]
    assert (result["code"], result["synthetic"], result["verified"]) == ("IMP-TEST-1001", False, False)
    assert result["label"] == "Pendiente de verificación"
    bad = SimpleUploadedFile("bad.json", b"{not json", content_type="application/json")
    resp = api.client.post(
        "/api/v1/tax/exogena/format-packs:import",
        {"file": bad},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="import-key-0002",
    )
    assert resp.status_code == 422
    missing_key = api.client.post("/api/v1/tax/exogena/format-packs:import", {}, format="multipart")
    assert missing_key.status_code == 400 and missing_key.json()["code"] == "idempotency_key_required"
