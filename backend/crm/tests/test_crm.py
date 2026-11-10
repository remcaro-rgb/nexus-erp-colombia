"""CRM: lead → customer conversion with consent, pipeline/opportunities, win/lose, forecast, PII masking."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from kernel.testing.tenancy import bound
from sales.models import Party
from sales.testing.support import ApiClient, World

pytestmark = pytest.mark.django_db
STAGES = [
    {"code": "PROSPECCION", "name": "Prospección", "probability": "10"},
    {"code": "PROPUESTA", "name": "Propuesta", "probability": "50"},
    {"code": "NEGOCIACION", "name": "Negociación", "probability": "80"},
]


def _ok(resp: Any, code: int = 200) -> dict[str, Any]:
    assert resp.status_code == code, resp.content
    return resp.json()


@pytest.fixture
def pipeline(crm_api: ApiClient) -> dict[str, Any]:
    return _ok(crm_api.post("pipelines", {"code": "B2B", "name": "Ventas B2B", "stages": STAGES}), 201)


def test_lead_conversion_requires_consent_and_creates_customer(
    crm_api: ApiClient, world: World, pipeline: dict[str, Any]
) -> None:
    lead = _ok(
        crm_api.post(
            "leads",
            {
                "name": "Ana Pérez (SINTÉTICA)",
                "company_name": "Ferretería Demo SAS",
                "nit": "900123456",
                "email": "ana@example.invalid",
            },
        ),
        201,
    )
    assert lead["lead_status"] == "NEW"
    refused = crm_api.post(f"leads/{lead['id']}:convert", {"parameters": {}})
    assert refused.status_code == 422 and "consent_ref" in refused.content.decode()
    consent = str(uuid.uuid4())
    params = {
        "consent_ref": consent,
        "opportunity": {
            "name": "Dotación 2027",
            "pipeline_id": pipeline["id"],
            "stage": "PROPUESTA",
            "amount": "5000000",
            "close_date": "2026-12-15",
        },
    }
    converted = _ok(crm_api.post(f"leads/{lead['id']}:convert", {"parameters": params}))
    assert converted["lead_status"] == "CONVERTED" and converted["party_id"] and converted["opportunity_id"]
    with bound(world.ctx("admin")):
        party = Party.objects.get(id=converted["party_id"])
    assert "customer" in party.roles and str(party.consent_ref) == consent and party.nit == "900123456"
    # a converted lead cannot be converted again
    again = crm_api.post(f"leads/{lead['id']}:convert", {"parameters": params})
    assert again.status_code == 409


def test_opportunity_win_lose_and_forecast(crm_api: ApiClient, world: World, pipeline: dict[str, Any]) -> None:
    def opp(name: str, stage: str, amount: str) -> dict[str, Any]:
        body = {
            "name": name,
            "pipeline_id": pipeline["id"],
            "stage": stage,
            "amount": amount,
            "close_date": "2026-12-10",
        }
        return _ok(crm_api.post("opportunities", body), 201)

    a, b, c = opp("A", "PROSPECCION", "1000000"), opp("B", "NEGOCIACION", "2000000"), opp("C", "PROPUESTA", "400000")
    assert Decimal(b["probability"]) == 80
    won = _ok(crm_api.post(f"opportunities/{b['id']}:win", {}))
    assert won["opportunity_status"] == "WON"
    assert crm_api.post(f"opportunities/{c['id']}:lose", {}).status_code == 422  # a reason is required
    _ok(crm_api.post(f"opportunities/{c['id']}:lose", {"reason": "precio"}))
    report = _ok(crm_api.get("forecast", period_from="2026-12-01", period_to="2026-12-31"))
    assert report["report"] == "crm.forecast"
    assert [(r["stage"], Decimal(r["amount"]), Decimal(r["weighted"])) for r in report["rows"]] == [
        ("PROSPECCION", Decimal(1000000), Decimal(100000))
    ]
    assert a["opportunity_status"] == "OPEN"


def test_crm_field_security_and_deny_by_default(crm_api: ApiClient, world: World) -> None:
    lead = _ok(
        crm_api.post(
            "leads", {"name": "Luis (SINTÉTICO)", "email": "luis@example.invalid", "phone": "+57 300 000 0000"}
        ),
        201,
    )
    seller = _ok(crm_api.as_("seller").get(f"leads/{lead['id']}"))
    assert "email" not in seller and "phone" not in seller and "name" not in seller
    assert crm_api.as_("outsider").get("leads").status_code == 403
    assert crm_api.as_("seller").get("forecast").status_code == 403  # amounts are confidential
    assert crm_api.as_("other_admin").get(f"leads/{lead['id']}").status_code in (403, 404)
