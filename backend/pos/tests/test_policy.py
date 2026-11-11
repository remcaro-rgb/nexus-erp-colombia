"""T-P12-10 — a cashier cannot see costs or other cashiers' sessions/tickets (row + field policy)."""

from __future__ import annotations

import pytest

from pos.testing.support import PosEnv, open_counter
from pos.tests.helpers import checkout_params
from sales.testing.support import ApiClient

pytestmark = pytest.mark.django_db


def test_T_P12_10_cashier_cannot_see_costs_or_other_sessions(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    session = open_counter(env)
    api.as_("cashier")
    p = checkout_params(
        session, [{"item_id": str(env.items["soda"].id), "quantity": "2"}], [{"type": "CASH", "amount": "5950"}]
    )
    r = api.post("tickets:checkout", {"parameters": p}, key=p["client_uuid"])
    assert r.status_code == 200, r.content
    ticket = r.json()["data"]
    body = r.content.decode()
    assert "cost_total" not in ticket and "unit_cost" not in body and "cost_value" not in body

    # cashier 2 sees neither the session nor the ticket of cashier 1 (lists empty, detail 404, Z report 404)
    api.as_("cashier2")
    assert api.get("sessions").json()["items"] == []
    assert api.get("tickets").json()["items"] == []
    assert api.get(f"tickets/{ticket['id']}").status_code == 404
    assert api.get(f"sessions/{session.id}").status_code == 404
    assert api.get("reports/z", session_id=str(session.id)).status_code == 404

    # the owner sees their own; the supervisor sees all of the company, with the cost fields
    api.as_("cashier")
    assert [x["id"] for x in api.get("tickets").json()["items"]] == [ticket["id"]]
    api.as_("supervisor")
    full = api.get(f"tickets/{ticket['id']}").json()
    assert full["cost_total"]["amount"] == "2000.0000" and full["lines"][0]["cost_value"]["amount"] == "2000.0000"
    assert len(api.get("sessions").json()["items"]) == 1

    # an outsider (no grant) gets 403 everywhere; another company's admin never sees company 1 rows
    api.as_("outsider")
    assert api.get("tickets").status_code == 403
    assert api.post("tickets:checkout", {"parameters": p}).status_code == 403
    api.as_("other_admin")
    assert api.get(f"tickets/{ticket['id']}").status_code in (403, 404)

    # the catalog snapshot never carries cost
    api.as_("cashier")
    snap = api.get("catalog-snapshot", config_id=str(env.config.id))
    assert snap.status_code == 200 and "cost" not in snap.content.decode()
